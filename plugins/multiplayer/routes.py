"""Multiplayer plugin -- WebSocket-based full-band multiplayer.

Creates rooms where players join, pick instruments, ready up, and play
together with synchronized playback. Each player's instrument stem is
auto-muted so they play their own part live.

WebSocket Protocol:
    Client -> Server:
        {"type": "join", "name": "PlayerName"}
        {"type": "set_instrument", "instrument": "guitar"|"bass"|"drums"|"vocals"}
        {"type": "ready", "ready": true|false}
        {"type": "start"}                           (host only)
        {"type": "sync_time", "currentTime": float}  (host broadcasts)
        {"type": "score_update", "score": {...}}
        {"type": "chat", "message": "text"}

    Server -> Client:
        {"type": "room_state", "state": {...}}
        {"type": "player_joined", "player": {...}}
        {"type": "player_left", "player_id": "..."}
        {"type": "game_start", "song_key": "...", "start_at": float}
        {"type": "time_sync", "currentTime": float, "serverTime": float}
        {"type": "score_broadcast", "player_id": "...", "score": {...}}
        {"type": "chat_broadcast", "player_id": "...", "name": "...", "message": "..."}
        {"type": "error", "message": "..."}
"""

import asyncio
import json
import time
import uuid
from pathlib import Path

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse


# ── Room State ──

# Room storage (in-memory; for production use Redis or similar)
rooms = {}  # { room_id: Room }

INSTRUMENTS = ["guitar", "bass", "drums", "vocals"]
MAX_PLAYERS_PER_ROOM = 4
SYNC_INTERVAL_SEC = 0.5


class Player:
    """A player in a room."""

    def __init__(self, player_id, name, ws):
        self.id = player_id
        self.name = name
        self.instrument = None
        self.ready = False
        self.ws = ws
        self.score = {"hits": 0, "misses": 0, "streak": 0, "accuracy": 0}

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "instrument": self.instrument,
            "ready": self.ready,
            "score": self.score,
        }


class Room:
    """A multiplayer room."""

    def __init__(self, room_id, song_key=None, host_name="Host"):
        self.id = room_id
        self.song_key = song_key
        self.host_name = host_name
        self.players = {}  # { player_id: Player }
        self.host_id = None
        self.playing = False
        self.current_time = 0.0
        self.start_time = None
        self.created_at = time.time()
        self._sync_task = None

    def to_dict(self):
        return {
            "room_id": self.id,
            "song_key": self.song_key,
            "players": [p.to_dict() for p in self.players.values()],
            "player_count": len(self.players),
            "playing": self.playing,
            "current_time": self.current_time,
            "host_id": self.host_id,
        }

    async def broadcast(self, message, exclude_id=None):
        """Send a message to all players in the room."""
        msg_str = json.dumps(message)
        disconnected = []
        for pid, player in self.players.items():
            if pid == exclude_id:
                continue
            try:
                await player.ws.send_text(msg_str)
            except Exception:
                disconnected.append(pid)

        # Clean up disconnected players
        for pid in disconnected:
            self.remove_player(pid)

    def add_player(self, player):
        self.players[player.id] = player
        if self.host_id is None:
            self.host_id = player.id

    def remove_player(self, player_id):
        if player_id in self.players:
            del self.players[player_id]
        # Reassign host if the host left
        if player_id == self.host_id and self.players:
            self.host_id = next(iter(self.players))
        elif not self.players:
            self.host_id = None

    def all_ready(self):
        if len(self.players) < 1:
            return False
        return all(p.ready for p in self.players.values())

    def get_muted_stems(self, player_id):
        """Return which stems should be muted for this player."""
        player = self.players.get(player_id)
        if not player or not player.instrument:
            return []
        # Mute the stem matching the player's instrument
        return [player.instrument]


def setup(app, context):
    config_dir = Path(context["config_dir"])
    log = context["log"]

    # ── REST Endpoints ──

    @app.post("/api/plugins/multiplayer/rooms")
    async def create_room(data: dict):
        """Create a new multiplayer room.

        Body: {"song_key": "...", "host_name": "PlayerName"}
        Returns: {"room_id": "...", "room": {...}}
        """
        song_key = data.get("song_key")
        host_name = data.get("host_name", "Host")

        room_id = str(uuid.uuid4())[:8]
        room = Room(room_id, song_key=song_key, host_name=host_name)
        rooms[room_id] = room

        log.info(f"Room created: {room_id} (song: {song_key})")
        return {"room_id": room_id, "room": room.to_dict()}

    @app.get("/api/plugins/multiplayer/rooms")
    async def list_rooms():
        """List active rooms."""
        # Clean up stale rooms (no players for > 5 minutes)
        stale = []
        for rid, room in rooms.items():
            if not room.players and time.time() - room.created_at > 300:
                stale.append(rid)
        for rid in stale:
            del rooms[rid]

        return {
            "rooms": [r.to_dict() for r in rooms.values()],
            "count": len(rooms),
        }

    @app.get("/api/plugins/multiplayer/rooms/{room_id}")
    async def get_room(room_id: str):
        """Get room details."""
        if room_id not in rooms:
            raise HTTPException(404, "Room not found")
        return rooms[room_id].to_dict()

    @app.delete("/api/plugins/multiplayer/rooms/{room_id}")
    async def delete_room(room_id: str):
        """Delete a room."""
        if room_id not in rooms:
            raise HTTPException(404, "Room not found")

        room = rooms[room_id]
        await room.broadcast({"type": "room_closed", "reason": "Room deleted by host"})

        # Cancel sync task if running
        if room._sync_task and not room._sync_task.done():
            room._sync_task.cancel()

        del rooms[room_id]
        log.info(f"Room deleted: {room_id}")
        return {"deleted": True}

    # ── WebSocket Endpoint ──

    @app.websocket("/ws/multiplayer/{room_id}")
    async def multiplayer_ws(ws: WebSocket, room_id: str):
        """WebSocket handler for a multiplayer room."""
        await ws.accept()

        if room_id not in rooms:
            await ws.send_text(json.dumps({
                "type": "error",
                "message": "Room not found",
            }))
            await ws.close()
            return

        room = rooms[room_id]

        if len(room.players) >= MAX_PLAYERS_PER_ROOM:
            await ws.send_text(json.dumps({
                "type": "error",
                "message": "Room is full",
            }))
            await ws.close()
            return

        player_id = str(uuid.uuid4())[:8]
        player = None

        try:
            # Wait for join message
            join_msg = await asyncio.wait_for(ws.receive_text(), timeout=10)
            join_data = json.loads(join_msg)

            if join_data.get("type") != "join":
                await ws.send_text(json.dumps({
                    "type": "error",
                    "message": "First message must be a join message",
                }))
                await ws.close()
                return

            player_name = join_data.get("name", f"Player {len(room.players) + 1}")
            player = Player(player_id, player_name, ws)
            room.add_player(player)

            log.info(f"Player {player_name} ({player_id}) joined room {room_id}")

            # Send current room state to the new player
            await ws.send_text(json.dumps({
                "type": "room_state",
                "state": room.to_dict(),
                "your_id": player_id,
                "is_host": room.host_id == player_id,
            }))

            # Notify other players
            await room.broadcast({
                "type": "player_joined",
                "player": player.to_dict(),
            }, exclude_id=player_id)

            # Main message loop
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                await handle_message(room, player, msg, log)

        except WebSocketDisconnect:
            pass
        except asyncio.TimeoutError:
            try:
                await ws.send_text(json.dumps({
                    "type": "error",
                    "message": "Join timeout",
                }))
            except Exception:
                pass
        except Exception as e:
            log.error(f"WebSocket error: {e}")
        finally:
            # Clean up
            if player:
                room.remove_player(player_id)
                log.info(
                    f"Player {player.name} ({player_id}) left room {room_id}"
                )
                await room.broadcast({
                    "type": "player_left",
                    "player_id": player_id,
                    "name": player.name,
                    "new_host_id": room.host_id,
                })

            # Delete empty rooms
            if not room.players and room_id in rooms:
                if room._sync_task and not room._sync_task.done():
                    room._sync_task.cancel()
                del rooms[room_id]
                log.info(f"Room {room_id} deleted (empty)")


async def handle_message(room, player, msg, log):
    """Handle a WebSocket message from a player."""
    msg_type = msg.get("type")

    if msg_type == "set_instrument":
        instrument = msg.get("instrument")
        if instrument not in INSTRUMENTS:
            await player.ws.send_text(json.dumps({
                "type": "error",
                "message": f"Invalid instrument. Choose from: {', '.join(INSTRUMENTS)}",
            }))
            return

        # Check if instrument is already taken
        for p in room.players.values():
            if p.id != player.id and p.instrument == instrument:
                await player.ws.send_text(json.dumps({
                    "type": "error",
                    "message": f"{instrument} is already taken by {p.name}",
                }))
                return

        player.instrument = instrument
        player.ready = False  # Reset ready when changing instrument
        log.info(f"Player {player.name} chose {instrument} in room {room.id}")

        await room.broadcast({
            "type": "room_state",
            "state": room.to_dict(),
        })

    elif msg_type == "ready":
        if not player.instrument:
            await player.ws.send_text(json.dumps({
                "type": "error",
                "message": "Choose an instrument first",
            }))
            return

        player.ready = msg.get("ready", True)

        await room.broadcast({
            "type": "room_state",
            "state": room.to_dict(),
        })

    elif msg_type == "start":
        if player.id != room.host_id:
            await player.ws.send_text(json.dumps({
                "type": "error",
                "message": "Only the host can start the game",
            }))
            return

        if not room.all_ready():
            await player.ws.send_text(json.dumps({
                "type": "error",
                "message": "Not all players are ready",
            }))
            return

        # Start the game -- broadcast start signal with a slight delay
        # so all clients can prepare
        room.playing = True
        room.start_time = time.time() + 2.0  # 2 second countdown
        room.current_time = 0.0

        # Tell each player which stems to mute
        for p in room.players.values():
            muted = room.get_muted_stems(p.id)
            await p.ws.send_text(json.dumps({
                "type": "game_start",
                "song_key": room.song_key,
                "start_at": room.start_time,
                "muted_stems": muted,
                "countdown": 2.0,
            }))

        log.info(f"Game started in room {room.id}")

        # Start periodic time sync from host
        if room._sync_task and not room._sync_task.done():
            room._sync_task.cancel()
        room._sync_task = asyncio.create_task(
            _sync_loop(room, log)
        )

    elif msg_type == "sync_time":
        # Host sends its current playback time; relay to all others
        if player.id == room.host_id:
            room.current_time = msg.get("currentTime", 0)
            await room.broadcast({
                "type": "time_sync",
                "currentTime": room.current_time,
                "serverTime": time.time(),
            }, exclude_id=player.id)

    elif msg_type == "score_update":
        player.score = msg.get("score", player.score)
        await room.broadcast({
            "type": "score_broadcast",
            "player_id": player.id,
            "name": player.name,
            "instrument": player.instrument,
            "score": player.score,
        }, exclude_id=player.id)

    elif msg_type == "chat":
        message = msg.get("message", "")
        if message:
            await room.broadcast({
                "type": "chat_broadcast",
                "player_id": player.id,
                "name": player.name,
                "message": message[:500],  # Limit chat message length
            })

    elif msg_type == "stop":
        if player.id == room.host_id:
            room.playing = False
            if room._sync_task and not room._sync_task.done():
                room._sync_task.cancel()
            await room.broadcast({"type": "game_stop"})

    elif msg_type == "set_song":
        if player.id == room.host_id:
            room.song_key = msg.get("song_key")
            room.playing = False
            # Reset all ready states
            for p in room.players.values():
                p.ready = False
            await room.broadcast({
                "type": "room_state",
                "state": room.to_dict(),
            })


async def _sync_loop(room, log):
    """Periodically request time sync from the host."""
    try:
        while room.playing and room.players:
            await asyncio.sleep(SYNC_INTERVAL_SEC)
            if room.host_id and room.host_id in room.players:
                host = room.players[room.host_id]
                try:
                    await host.ws.send_text(json.dumps({
                        "type": "request_sync",
                    }))
                except Exception:
                    break
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error(f"Sync loop error in room {room.id}: {e}")
