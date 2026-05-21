"""Vocal Highway plugin -- backend routes.

Serves parsed vocal chart data (lyrics + pitch + timing) from combined
packages. Handles YARC/Clone Hero vocal sections from .chart files.

Vocal events in Clone Hero .chart files appear in sections like
[ExpertVocals] with two types of entries:
  - Lyric events: tick = E "lyric text"
  - Pitch/note events: tick = N <midi_note> <duration>

The lyrics and pitch events are matched by tick position -- a lyric event
at the same tick as a note event means "sing this text at this pitch
for this duration."
"""

import json
import re
from pathlib import Path

from fastapi import HTTPException


def setup(app, context):
    config_dir = Path(context["config_dir"])
    log = context["log"]

    @app.get("/api/plugins/vocal_highway/chart/{song_key}")
    async def get_vocal_chart(song_key: str):
        """Return parsed vocal chart data for a song.

        Returns: {
            "vocals": [
                {"time": float, "duration": float, "pitch": int, "lyric": str},
                ...
            ],
            "metadata": {...}
        }
        """
        chart_data = _find_vocal_chart(song_key)
        if chart_data is None:
            raise HTTPException(404, f"No vocal chart found for '{song_key}'")
        return chart_data

    @app.get("/api/plugins/vocal_highway/songs")
    async def list_vocal_songs():
        """List all songs that have vocal charts available."""
        songs = []

        combined_dir = config_dir / "combined"
        if combined_dir.exists():
            for song_dir in combined_dir.iterdir():
                vocal_file = song_dir / "vocals" / "chart_data.json"
                if vocal_file.exists():
                    try:
                        data = json.loads(vocal_file.read_text())
                        meta = data.get("metadata", {})
                        songs.append({
                            "key": song_dir.name,
                            "name": meta.get("Name", meta.get("name", song_dir.name)),
                            "artist": meta.get("Artist", meta.get("artist", "")),
                            "event_count": len(data.get("vocals", [])),
                        })
                    except json.JSONDecodeError:
                        continue

        # Also check drum chart directories for songs that might have
        # vocals embedded in the same .chart file
        stem_index_path = config_dir / "stem_index.json"
        if stem_index_path.exists():
            try:
                index = json.loads(stem_index_path.read_text())
                for song_key, entry in index.items():
                    stems_dir = Path(entry.get("stems_dir", ""))
                    for search_dir in [
                        stems_dir.parent,
                        stems_dir.parent / "vocals",
                    ]:
                        chart_file = search_dir / "vocal_chart_data.json"
                        if chart_file.exists():
                            try:
                                data = json.loads(chart_file.read_text())
                                if data.get("vocals"):
                                    songs.append({
                                        "key": song_key,
                                        "name": data.get("metadata", {}).get(
                                            "Name", song_key
                                        ),
                                        "artist": data.get("metadata", {}).get(
                                            "Artist", ""
                                        ),
                                        "event_count": len(data["vocals"]),
                                    })
                            except json.JSONDecodeError:
                                continue
            except (json.JSONDecodeError, OSError):
                pass

        return {"songs": songs, "count": len(songs)}

    def _find_vocal_chart(song_key: str):
        """Search for vocal chart data in known locations."""
        # Check combined packages first
        combined_dir = config_dir / "combined"
        if combined_dir.exists():
            vocal_file = combined_dir / song_key / "vocals" / "chart_data.json"
            if vocal_file.exists():
                return json.loads(vocal_file.read_text())

        # Check stem index for song location, then look for chart files
        stem_index_path = config_dir / "stem_index.json"
        if stem_index_path.exists():
            index = json.loads(stem_index_path.read_text())
            if song_key in index:
                stems_dir = Path(index[song_key].get("stems_dir", ""))

                # Look for pre-parsed vocal chart
                for search_dir in [
                    stems_dir.parent,
                    stems_dir.parent / "vocals",
                    stems_dir / ".." / "vocals",
                ]:
                    for fname in [
                        "vocal_chart_data.json",
                        "chart_data.json",
                    ]:
                        fpath = search_dir / fname
                        if fpath.exists():
                            data = json.loads(fpath.read_text())
                            if data.get("vocals"):
                                return data

                # Look for raw .chart file and parse vocals from it
                for search_dir in [
                    stems_dir.parent,
                    stems_dir.parent / "drums",
                ]:
                    for name in [
                        "notes.chart",
                        "Notes.chart",
                    ]:
                        chart_path = search_dir / name
                        if chart_path.exists():
                            parsed = parse_chart_vocals(chart_path)
                            if parsed and parsed.get("vocals"):
                                return parsed

        return None


def parse_chart_vocals(chart_path: Path) -> dict:
    """Parse vocal events from a Clone Hero .chart file.

    Looks for sections named [ExpertVocals], [HardVocals], etc.
    Also handles [Events] section which sometimes contains lyric data.

    Returns: {
        "metadata": {...},
        "vocals": [
            {"time": float, "duration": float, "pitch": int, "lyric": str},
            ...
        ]
    }
    """
    text = chart_path.read_text(encoding="utf-8", errors="replace")

    metadata = {}
    sync_track = []
    vocal_notes = {}    # tick -> {note, duration}
    vocal_lyrics = {}   # tick -> lyric_text
    event_lyrics = {}   # tick -> lyric_text (from [Events] section)

    current_section = None

    for line in text.split("\n"):
        line = line.strip()

        # Section headers
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            continue

        if line in ("{", "}"):
            continue

        # Metadata
        if current_section == "Song":
            match = re.match(r'(\w+)\s*=\s*"?(.+?)"?\s*$', line)
            if match:
                metadata[match.group(1)] = match.group(2)

        # Sync track for tick->time conversion
        elif current_section == "SyncTrack":
            match = re.match(r"(\d+)\s*=\s*B\s+(\d+)", line)
            if match:
                tick = int(match.group(1))
                bpm = int(match.group(2)) / 1000.0
                sync_track.append({"tick": tick, "bpm": bpm})

        # Vocal sections
        elif current_section and "Vocals" in current_section:
            # Note events: tick = N <midi_note> <duration>
            match = re.match(r"(\d+)\s*=\s*N\s+(\d+)\s+(\d+)", line)
            if match:
                tick = int(match.group(1))
                note = int(match.group(2))
                duration = int(match.group(3))
                vocal_notes[tick] = {"note": note, "duration": duration}
                continue

            # Lyric events: tick = E "lyric text"
            match = re.match(r'(\d+)\s*=\s*E\s+"(.+?)"', line)
            if match:
                tick = int(match.group(1))
                lyric = match.group(2)
                vocal_lyrics[tick] = lyric
                continue

            # Alternative lyric format: tick = E lyric text (no quotes)
            match = re.match(r"(\d+)\s*=\s*E\s+lyric\s+(.*)", line)
            if match:
                tick = int(match.group(1))
                lyric = match.group(2).strip()
                vocal_lyrics[tick] = lyric

        # Events section can also contain lyrics
        elif current_section == "Events":
            match = re.match(r'(\d+)\s*=\s*E\s+"lyric\s+(.*?)"', line)
            if match:
                tick = int(match.group(1))
                lyric = match.group(2).strip()
                event_lyrics[tick] = lyric

    # Merge event lyrics if no dedicated vocal lyrics found
    if not vocal_lyrics and event_lyrics:
        vocal_lyrics = event_lyrics

    if not vocal_notes and not vocal_lyrics:
        return {"metadata": metadata, "vocals": []}

    # Convert to timed events
    resolution = int(metadata.get("Resolution", 192))
    vocals = []

    # Match notes with lyrics by tick
    all_ticks = sorted(set(list(vocal_notes.keys()) + list(vocal_lyrics.keys())))

    for tick in all_ticks:
        note_info = vocal_notes.get(tick)
        lyric = vocal_lyrics.get(tick, "")

        if note_info:
            time_sec = _ticks_to_seconds(tick, sync_track, resolution)
            dur_sec = _ticks_to_seconds(
                tick + note_info["duration"], sync_track, resolution
            ) - time_sec

            vocals.append({
                "time": round(time_sec, 4),
                "duration": round(max(dur_sec, 0.05), 4),
                "pitch": note_info["note"],
                "lyric": lyric,
            })
        elif lyric:
            # Lyric without a pitch note -- spoken or unpitched
            time_sec = _ticks_to_seconds(tick, sync_track, resolution)
            vocals.append({
                "time": round(time_sec, 4),
                "duration": 0.1,
                "pitch": 0,
                "lyric": lyric,
            })

    vocals.sort(key=lambda v: v["time"])

    return {"metadata": metadata, "vocals": vocals}


def _ticks_to_seconds(tick: int, sync_track: list, resolution: int = 192) -> float:
    """Convert chart ticks to seconds using BPM data.

    Same algorithm as combine_charts.py / midi_parser.py.
    """
    if not sync_track:
        return tick / resolution * 0.5

    bpm_events = [e for e in sync_track if "bpm" in e]
    if not bpm_events:
        return tick / resolution * 0.5

    seconds = 0.0
    last_tick = 0
    last_bpm = bpm_events[0]["bpm"] if bpm_events else 120.0

    for event in bpm_events:
        if event["tick"] > tick:
            break
        delta_ticks = event["tick"] - last_tick
        seconds += (delta_ticks / resolution) * (60.0 / last_bpm)
        last_tick = event["tick"]
        last_bpm = event["bpm"]

    delta_ticks = tick - last_tick
    seconds += (delta_ticks / resolution) * (60.0 / last_bpm)

    return seconds
