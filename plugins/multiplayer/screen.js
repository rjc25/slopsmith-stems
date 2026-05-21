/**
 * Multiplayer Plugin — WebSocket-based full-band multiplayer.
 *
 * Provides room creation/joining, instrument selection, ready-up,
 * synchronized playback, and split-screen highway layout for
 * local or networked multiplayer sessions.
 *
 * Flow:
 *   1. Create or join a room
 *   2. Each player picks an instrument (guitar/bass/drums/vocals)
 *   3. All players ready up
 *   4. Host starts the song -> synchronized playback
 *   5. Each player's instrument stem is auto-muted
 *   6. Split-screen layout shows each player's highway
 */

(function () {
  "use strict";

  // ── State ──
  var ws = null;
  var roomId = null;
  var playerId = null;
  var isHost = false;
  var roomState = null;
  var playerName = localStorage.getItem("mp_player_name") || "";
  var connected = false;
  var gameActive = false;
  var countdownTimer = null;

  // ── UI Elements ──
  var container = null;

  // ── Room Management ──

  async function createRoom(songKey) {
    try {
      var name = playerName || "Host";
      var resp = await fetch("/api/plugins/multiplayer/rooms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ song_key: songKey, host_name: name }),
      });
      var data = await resp.json();
      roomId = data.room_id;
      connectWebSocket(roomId, name);
    } catch (err) {
      showError("Failed to create room: " + err.message);
    }
  }

  async function fetchRooms() {
    try {
      var resp = await fetch("/api/plugins/multiplayer/rooms");
      var data = await resp.json();
      return data.rooms || [];
    } catch (err) {
      showError("Failed to list rooms: " + err.message);
      return [];
    }
  }

  function joinRoom(id, name) {
    roomId = id;
    connectWebSocket(id, name);
  }

  // ── WebSocket ──

  function connectWebSocket(roomIdVal, name) {
    var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    var wsUrl = protocol + "//" + window.location.host + "/ws/multiplayer/" + roomIdVal;

    ws = new WebSocket(wsUrl);

    ws.onopen = function () {
      connected = true;
      ws.send(JSON.stringify({ type: "join", name: name }));
    };

    ws.onmessage = function (event) {
      var msg = JSON.parse(event.data);
      handleServerMessage(msg);
    };

    ws.onclose = function () {
      connected = false;
      if (gameActive) {
        showError("Disconnected from room");
        gameActive = false;
      }
      renderUI();
    };

    ws.onerror = function () {
      connected = false;
      showError("WebSocket connection failed");
    };
  }

  function sendMessage(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }

  function disconnect() {
    if (ws) {
      ws.close();
      ws = null;
    }
    roomId = null;
    playerId = null;
    isHost = false;
    roomState = null;
    connected = false;
    gameActive = false;
    renderUI();
  }

  // ── Message Handlers ──

  function handleServerMessage(msg) {
    switch (msg.type) {
      case "room_state":
        roomState = msg.state;
        if (msg.your_id) playerId = msg.your_id;
        if (msg.is_host !== undefined) isHost = msg.is_host;
        renderUI();
        break;

      case "player_joined":
        if (roomState) {
          roomState.players.push(msg.player);
          roomState.player_count = roomState.players.length;
        }
        renderUI();
        addChatMessage("System", msg.player.name + " joined the room");
        break;

      case "player_left":
        if (roomState) {
          roomState.players = roomState.players.filter(function (p) {
            return p.id !== msg.player_id;
          });
          roomState.player_count = roomState.players.length;
          if (msg.new_host_id) {
            roomState.host_id = msg.new_host_id;
            isHost = msg.new_host_id === playerId;
          }
        }
        renderUI();
        addChatMessage("System", (msg.name || "A player") + " left");
        break;

      case "game_start":
        gameActive = true;
        startCountdown(msg.countdown || 2, msg.song_key, msg.muted_stems);
        break;

      case "time_sync":
        if (gameActive) {
          syncPlayback(msg.currentTime, msg.serverTime);
        }
        break;

      case "request_sync":
        // Host: send current playback time back
        if (isHost) {
          var audio = document.querySelector("audio") || document.getElementById("main-audio");
          if (audio) {
            sendMessage({ type: "sync_time", currentTime: audio.currentTime });
          }
        }
        break;

      case "score_broadcast":
        updatePlayerScore(msg.player_id, msg.score, msg.name, msg.instrument);
        break;

      case "chat_broadcast":
        addChatMessage(msg.name, msg.message);
        break;

      case "game_stop":
        gameActive = false;
        renderUI();
        addChatMessage("System", "Game stopped");
        break;

      case "room_closed":
        disconnect();
        showError("Room was closed: " + (msg.reason || ""));
        break;

      case "error":
        showError(msg.message);
        break;
    }
  }

  // ── Game Control ──

  function startCountdown(seconds, songKey, mutedStems) {
    // Mute the player's instrument stem
    if (window.slopsmith && mutedStems) {
      mutedStems.forEach(function (stem) {
        window.slopsmith.emit("stems:mute", { stem: stem });
      });
    }

    var remaining = Math.ceil(seconds);
    renderCountdown(remaining);

    countdownTimer = setInterval(function () {
      remaining--;
      if (remaining <= 0) {
        clearInterval(countdownTimer);
        countdownTimer = null;
        // Start playback
        if (window.playSong && songKey) {
          window.playSong({ key: songKey, songKey: songKey });
        }
        renderGameView();
      } else {
        renderCountdown(remaining);
      }
    }, 1000);
  }

  function syncPlayback(targetTime, serverTime) {
    if (isHost) return; // Host is the time authority

    var audio = document.querySelector("audio") || document.getElementById("main-audio");
    if (!audio) return;

    // Account for network latency (rough estimate)
    var latency = (Date.now() / 1000 - serverTime) * 0.5;
    var adjustedTime = targetTime + latency;

    // Only seek if drift exceeds tolerance
    if (Math.abs(audio.currentTime - adjustedTime) > 0.1) {
      audio.currentTime = adjustedTime;
    }
  }

  function updatePlayerScore(pid, score, name, instrument) {
    // Update roomState with the score
    if (roomState) {
      var player = roomState.players.find(function (p) { return p.id === pid; });
      if (player) {
        player.score = score;
      }
    }
    renderScoreboard();
  }

  // ── UI Rendering ──

  function getContainer() {
    if (!container) {
      container = document.getElementById("multiplayer-ui");
      if (!container) {
        container = document.createElement("div");
        container.id = "multiplayer-ui";
        container.style.cssText =
          "position:fixed; top:0; left:0; right:0; bottom:0; z-index:9998;" +
          "background:#0d0d1a; color:white; font-family:system-ui,-apple-system,sans-serif;" +
          "overflow-y:auto; display:none;";
        document.body.appendChild(container);
      }
    }
    return container;
  }

  function showUI() {
    var c = getContainer();
    c.style.display = "block";
    renderUI();
  }

  function hideUI() {
    var c = getContainer();
    c.style.display = "none";
  }

  function renderUI() {
    var c = getContainer();

    if (!connected || !roomState) {
      renderLobby(c);
    } else if (gameActive) {
      renderGameView();
    } else {
      renderRoomView(c);
    }
  }

  function renderLobby(c) {
    c.innerHTML =
      '<div style="max-width:600px;margin:0 auto;padding:40px 20px;">' +
      '  <h1 style="font-size:28px;font-weight:bold;margin-bottom:8px;">Multiplayer</h1>' +
      '  <p style="color:#888;margin-bottom:30px;">Full-band multiplayer sessions</p>' +
      '  <div style="margin-bottom:30px;">' +
      '    <label style="display:block;font-size:13px;color:#888;margin-bottom:6px;">Your Name</label>' +
      '    <input id="mp-name" type="text" value="' + escapeHtml(playerName) + '"' +
      '      placeholder="Enter your name" ' +
      '      style="width:100%;padding:10px;border-radius:8px;border:1px solid #333;background:#1a1a2e;color:white;font-size:15px;">' +
      '  </div>' +
      '  <div style="display:flex;gap:12px;margin-bottom:40px;">' +
      '    <button onclick="window._mpCreateRoom()" ' +
      '      style="flex:1;padding:12px;border-radius:8px;border:none;background:#3B82F6;color:white;font-size:15px;cursor:pointer;font-weight:bold;">' +
      '      Create Room</button>' +
      '    <button onclick="window._mpRefreshRooms()" ' +
      '      style="padding:12px 20px;border-radius:8px;border:1px solid #333;background:#1a1a2e;color:white;font-size:14px;cursor:pointer;">' +
      '      Refresh</button>' +
      '  </div>' +
      '  <h2 style="font-size:18px;font-weight:bold;margin-bottom:12px;">Open Rooms</h2>' +
      '  <div id="mp-room-list" style="color:#888;font-size:14px;">Loading...</div>' +
      '  <div style="margin-top:40px;text-align:center;">' +
      '    <button onclick="window._mpClose()" ' +
      '      style="padding:8px 20px;border-radius:8px;border:1px solid #333;background:transparent;color:#888;font-size:13px;cursor:pointer;">' +
      '      Close</button>' +
      '  </div>' +
      '</div>';

    refreshRoomList();
  }

  function renderRoomView(c) {
    if (!roomState) return;

    var playersHtml = "";
    var instruments = ["guitar", "bass", "drums", "vocals"];
    var instrumentIcons = { guitar: "&#127928;", bass: "&#127928;", drums: "&#129345;", vocals: "&#127908;" };
    var instrumentLabels = { guitar: "Guitar", bass: "Bass", drums: "Drums", vocals: "Vocals" };

    for (var i = 0; i < roomState.players.length; i++) {
      var p = roomState.players[i];
      var isMe = p.id === playerId;
      var isPlayerHost = p.id === roomState.host_id;
      var bgColor = isMe ? "#1e3a5f" : "#1a1a2e";
      var border = isMe ? "border:1px solid #3B82F6;" : "border:1px solid #333;";

      playersHtml +=
        '<div style="padding:12px;border-radius:8px;' + border + 'background:' + bgColor + ';margin-bottom:8px;">' +
        '  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">' +
        '    <span style="font-weight:bold;">' + escapeHtml(p.name) +
        (isPlayerHost ? ' <span style="color:#FFD700;font-size:11px;">(Host)</span>' : "") + '</span>' +
        '    <span style="font-size:12px;' + (p.ready ? 'color:#22C55E;"' : 'color:#888;"') + '>' +
        (p.ready ? "READY" : "Not Ready") + '</span>' +
        '  </div>';

      if (isMe) {
        // Instrument selection buttons
        playersHtml += '  <div style="display:flex;gap:6px;margin-bottom:8px;">';
        for (var j = 0; j < instruments.length; j++) {
          var inst = instruments[j];
          var taken = false;
          for (var k = 0; k < roomState.players.length; k++) {
            if (roomState.players[k].id !== playerId && roomState.players[k].instrument === inst) {
              taken = true;
              break;
            }
          }
          var selected = p.instrument === inst;
          var btnStyle = selected
            ? "background:#3B82F6;color:white;border:1px solid #60A5FA;"
            : taken
              ? "background:#1a1a2e;color:#444;border:1px solid #222;cursor:not-allowed;"
              : "background:#1a1a2e;color:#ccc;border:1px solid #444;cursor:pointer;";

          playersHtml +=
            '    <button onclick="window._mpSetInstrument(\'' + inst + '\')" ' +
            (taken && !selected ? 'disabled ' : '') +
            '      style="flex:1;padding:8px;border-radius:6px;font-size:12px;' + btnStyle + '">' +
            instrumentIcons[inst] + " " + instrumentLabels[inst] +
            (taken ? " (taken)" : "") +
            '    </button>';
        }
        playersHtml += '  </div>';

        // Ready button
        playersHtml +=
          '  <button onclick="window._mpToggleReady()" ' +
          '    style="width:100%;padding:8px;border-radius:6px;border:none;font-size:14px;cursor:pointer;font-weight:bold;' +
          (p.ready
            ? 'background:#22C55E33;color:#22C55E;border:1px solid #22C55E;"'
            : 'background:#3B82F633;color:#3B82F6;border:1px solid #3B82F6;"') + '>' +
          (p.ready ? "Cancel Ready" : "Ready Up") +
          '  </button>';
      } else {
        // Other player's instrument
        playersHtml +=
          '  <div style="font-size:13px;color:#888;">' +
          (p.instrument
            ? instrumentIcons[p.instrument] + " " + instrumentLabels[p.instrument]
            : "Choosing instrument...") +
          '  </div>';
      }

      playersHtml += '</div>';
    }

    // Room code
    var roomCodeHtml =
      '<div style="background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:12px;margin-bottom:20px;text-align:center;">' +
      '  <div style="font-size:11px;color:#888;margin-bottom:4px;">ROOM CODE</div>' +
      '  <div style="font-size:28px;font-weight:bold;letter-spacing:4px;font-family:monospace;">' +
      escapeHtml(roomState.room_id) + '</div>' +
      '  <div style="font-size:12px;color:#888;margin-top:4px;">Share this code with bandmates</div>' +
      '</div>';

    // Start button (host only)
    var startHtml = "";
    if (isHost) {
      var allReady = roomState.players.length > 0 &&
        roomState.players.every(function (p) { return p.ready; });
      startHtml =
        '<button onclick="window._mpStart()" ' +
        (allReady ? '' : 'disabled ') +
        '  style="width:100%;padding:14px;border-radius:8px;border:none;font-size:16px;cursor:pointer;' +
        '  font-weight:bold;margin-top:16px;' +
        (allReady
          ? 'background:#22C55E;color:white;"'
          : 'background:#333;color:#666;cursor:not-allowed;"') + '>' +
        'Start Game' +
        '</button>';
    }

    // Chat
    var chatHtml =
      '<div style="margin-top:20px;border-top:1px solid #333;padding-top:12px;">' +
      '  <div id="mp-chat-log" style="height:100px;overflow-y:auto;font-size:12px;color:#888;margin-bottom:8px;"></div>' +
      '  <div style="display:flex;gap:6px;">' +
      '    <input id="mp-chat-input" type="text" placeholder="Chat..." ' +
      '      onkeydown="if(event.key===\'Enter\')window._mpSendChat()" ' +
      '      style="flex:1;padding:8px;border-radius:6px;border:1px solid #333;background:#0d0d1a;color:white;font-size:13px;">' +
      '    <button onclick="window._mpSendChat()" ' +
      '      style="padding:8px 14px;border-radius:6px;border:none;background:#3B82F6;color:white;font-size:13px;cursor:pointer;">' +
      '      Send</button>' +
      '  </div>' +
      '</div>';

    c.innerHTML =
      '<div style="max-width:600px;margin:0 auto;padding:20px;">' +
      '  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">' +
      '    <h1 style="font-size:22px;font-weight:bold;">Room Lobby</h1>' +
      '    <button onclick="window._mpLeave()" ' +
      '      style="padding:6px 14px;border-radius:6px;border:1px solid #444;background:transparent;color:#888;font-size:12px;cursor:pointer;">' +
      '      Leave</button>' +
      '  </div>' +
      roomCodeHtml +
      '  <div style="margin-bottom:12px;">' +
      '    <span style="font-size:13px;color:#888;">Song: </span>' +
      '    <span style="font-size:14px;font-weight:bold;">' +
      escapeHtml(roomState.song_key || "(none selected)") + '</span>' +
      '  </div>' +
      '  <h2 style="font-size:16px;font-weight:bold;margin-bottom:10px;">Players (' +
      roomState.player_count + '/4)</h2>' +
      playersHtml +
      startHtml +
      chatHtml +
      '</div>';
  }

  function renderCountdown(seconds) {
    var c = getContainer();
    c.innerHTML =
      '<div style="display:flex;justify-content:center;align-items:center;height:100vh;">' +
      '  <div style="text-align:center;">' +
      '    <div style="font-size:120px;font-weight:bold;color:#3B82F6;">' + seconds + '</div>' +
      '    <div style="font-size:20px;color:#888;">Get Ready!</div>' +
      '  </div>' +
      '</div>';
  }

  function renderGameView() {
    if (!roomState) return;
    var c = getContainer();

    // Build split-screen layout based on player count
    var playerCount = roomState.players.length;
    var gridStyle = "";

    if (playerCount <= 1) {
      gridStyle = "grid-template-columns:1fr;grid-template-rows:1fr;";
    } else if (playerCount === 2) {
      gridStyle = "grid-template-columns:1fr;grid-template-rows:1fr 1fr;";
    } else {
      gridStyle = "grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;";
    }

    var panelsHtml = "";
    for (var i = 0; i < roomState.players.length; i++) {
      var p = roomState.players[i];
      var isMe = p.id === playerId;
      var borderColor = isMe ? "#3B82F6" : "#333";
      var instrumentLabels = { guitar: "Guitar", bass: "Bass", drums: "Drums", vocals: "Vocals" };

      panelsHtml +=
        '<div style="border:2px solid ' + borderColor + ';border-radius:8px;overflow:hidden;position:relative;background:#0d0d1a;">' +
        '  <div style="position:absolute;top:8px;left:10px;z-index:10;font-size:12px;background:#00000088;padding:4px 10px;border-radius:4px;">' +
        escapeHtml(p.name) + ' &mdash; ' + (instrumentLabels[p.instrument] || "?") +
        '  </div>' +
        '  <div id="mp-panel-' + p.id + '" style="width:100%;height:100%;"></div>' +
        '  <div id="mp-score-' + p.id + '" style="position:absolute;bottom:8px;right:10px;font-size:13px;color:#888;"></div>' +
        '</div>';
    }

    c.innerHTML =
      '<div style="display:grid;' + gridStyle + 'height:100vh;gap:4px;padding:4px;">' +
      panelsHtml +
      '</div>';
  }

  function renderScoreboard() {
    if (!roomState) return;
    for (var i = 0; i < roomState.players.length; i++) {
      var p = roomState.players[i];
      var el = document.getElementById("mp-score-" + p.id);
      if (el && p.score) {
        var acc = p.score.hits + p.score.misses > 0
          ? Math.round(p.score.hits / (p.score.hits + p.score.misses) * 100)
          : 0;
        el.textContent = acc + "% (" + p.score.hits + "/" + (p.score.hits + p.score.misses) + ")";
        el.style.color = acc >= 90 ? "#22C55E" : acc >= 70 ? "#EAB308" : "#EF4444";
      }
    }
  }

  // ── Room list ──

  async function refreshRoomList() {
    var el = document.getElementById("mp-room-list");
    if (!el) return;

    var rooms = await fetchRooms();
    if (rooms.length === 0) {
      el.innerHTML = '<p style="color:#666;">No open rooms. Create one to get started.</p>';
      return;
    }

    var html = "";
    for (var i = 0; i < rooms.length; i++) {
      var r = rooms[i];
      html +=
        '<div style="display:flex;justify-content:space-between;align-items:center;padding:10px;' +
        'border:1px solid #333;border-radius:8px;margin-bottom:6px;background:#1a1a2e;">' +
        '  <div>' +
        '    <span style="font-weight:bold;color:white;">' + escapeHtml(r.room_id) + '</span>' +
        '    <span style="color:#888;font-size:12px;margin-left:8px;">' +
        escapeHtml(r.song_key || "No song") + ' &mdash; ' + r.player_count + '/4 players</span>' +
        '  </div>' +
        '  <button onclick="window._mpJoinRoom(\'' + escapeHtml(r.room_id) + '\')" ' +
        '    style="padding:6px 14px;border-radius:6px;border:none;background:#3B82F6;color:white;font-size:13px;cursor:pointer;">' +
        '    Join</button>' +
        '</div>';
    }
    el.innerHTML = html;
  }

  // ── Chat ──

  var chatLog = [];

  function addChatMessage(name, message) {
    chatLog.push({ name: name, message: message, time: new Date() });
    if (chatLog.length > 50) chatLog.shift();

    var el = document.getElementById("mp-chat-log");
    if (el) {
      var timeStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      el.innerHTML += '<div><span style="color:#555;">[' + timeStr + ']</span> ' +
        '<span style="color:#888;">' + escapeHtml(name) + ':</span> ' +
        escapeHtml(message) + '</div>';
      el.scrollTop = el.scrollHeight;
    }
  }

  // ── Global handlers (called from onclick) ──

  window._mpCreateRoom = function () {
    var nameEl = document.getElementById("mp-name");
    playerName = nameEl ? nameEl.value.trim() : "Player";
    if (!playerName) playerName = "Player";
    localStorage.setItem("mp_player_name", playerName);
    createRoom(null);
  };

  window._mpRefreshRooms = refreshRoomList;

  window._mpJoinRoom = function (id) {
    var nameEl = document.getElementById("mp-name");
    playerName = nameEl ? nameEl.value.trim() : "Player";
    if (!playerName) playerName = "Player";
    localStorage.setItem("mp_player_name", playerName);
    joinRoom(id, playerName);
  };

  window._mpSetInstrument = function (inst) {
    sendMessage({ type: "set_instrument", instrument: inst });
  };

  window._mpToggleReady = function () {
    var me = roomState && roomState.players.find(function (p) { return p.id === playerId; });
    sendMessage({ type: "ready", ready: !(me && me.ready) });
  };

  window._mpStart = function () {
    sendMessage({ type: "start" });
  };

  window._mpLeave = disconnect;

  window._mpClose = hideUI;

  window._mpSendChat = function () {
    var input = document.getElementById("mp-chat-input");
    if (input && input.value.trim()) {
      sendMessage({ type: "chat", message: input.value.trim() });
      input.value = "";
    }
  };

  // ── Helpers ──

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showError(msg) {
    console.error("[Multiplayer] " + msg);
    // Could show a toast notification here
  }

  // ── SlopSmith Integration ──

  // Listen for multiplayer nav click
  if (window.slopsmith) {
    window.slopsmith.on("nav:multiplayer", function () {
      showUI();
    });

    // Periodically send score updates during gameplay
    window.slopsmith.on("score:update", function (data) {
      if (gameActive && connected) {
        sendMessage({ type: "score_update", score: data });
      }
    });
  }

  // Also expose for direct invocation
  window.slopsmithMultiplayer = {
    show: showUI,
    hide: hideUI,
    createRoom: createRoom,
    joinRoom: joinRoom,
    disconnect: disconnect,
  };

  console.log("[Multiplayer] Plugin loaded. Call window.slopsmithMultiplayer.show() to open.");
})();
