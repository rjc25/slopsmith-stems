/**
 * Drum Highway Renderer — Clone Hero-faithful drum lane visualization.
 *
 * Renders a scrolling drum highway with colored lanes matching Clone Hero's
 * drum chart style. Supports Expert/Hard/Medium/Easy difficulties and
 * pro drums (cymbal vs tom distinction).
 *
 * Lane layout (left to right):
 *   Kick (orange bar at bottom) | Red (snare) | Yellow (hi-hat) | Blue (tom) | Green (floor tom)
 *   With pro drums: cymbals render as diamond shapes, toms as circles
 *
 * Colors match Clone Hero exactly:
 *   Kick:   #FF8800 (orange)
 *   Red:    #FF0000 (snare)
 *   Yellow: #FFFF00 (hi-hat / yellow cymbal)
 *   Blue:   #0088FF (blue tom / blue cymbal)
 *   Green:  #00FF00 (floor tom / green cymbal)
 */

(function () {
  "use strict";

  // ── Constants (Clone Hero-accurate colors) ──
  const LANE_COLORS = {
    kick: { fill: "#FF8800", glow: "#FF880066", name: "Kick" },
    red: { fill: "#FF0000", glow: "#FF000066", name: "Snare" },
    yellow: { fill: "#FFFF00", glow: "#FFFF0066", name: "Hi-Hat" },
    blue: { fill: "#0088FF", glow: "#0088FF66", name: "Blue Tom" },
    green: { fill: "#00FF00", glow: "#00FF0066", name: "Floor Tom" },
    orange: { fill: "#FF8800", glow: "#FF880066", name: "Cymbal" },
  };

  // Lane order (left to right, matching Clone Hero)
  const LANES = ["red", "yellow", "blue", "green"];
  const KICK_LANE = "kick";

  // Visual settings
  const HIGHWAY_BG = "#1a1a2e";
  const LANE_LINE_COLOR = "#333355";
  const STRIKELINE_COLOR = "#FFFFFF";
  const STRIKELINE_Y_RATIO = 0.85; // Strike line position (bottom 15%)
  const NOTE_RADIUS = 18;
  const KICK_HEIGHT = 8;
  const LOOK_AHEAD_SECONDS = 2.5;
  const NOTE_SPEED = 1.0; // Multiplier

  // ── Drum Chart Data ──
  let drumNotes = [];
  let currentDifficulty = "expert";
  let currentSongKey = null;

  async function loadDrumChart(songKey) {
    try {
      const resp = await fetch(
        `/api/plugins/drum_highway/chart/${encodeURIComponent(songKey)}`
      );
      if (!resp.ok) return false;

      const data = await resp.json();
      if (!data.drums || !data.drums[currentDifficulty]) return false;

      drumNotes = data.drums[currentDifficulty];
      currentSongKey = songKey;
      console.log(
        `[DrumHighway] Loaded ${drumNotes.length} notes (${currentDifficulty})`
      );
      return true;
    } catch (err) {
      console.error("[DrumHighway] Failed to load chart:", err);
      return false;
    }
  }

  // ── Renderer Factory (slopsmith viz contract) ──

  window.slopsmithViz_drum_highway = function () {
    let ctx = null;
    let canvas = null;
    let width = 0;
    let height = 0;

    return {
      contextType: "2d",

      init(c, bundle) {
        canvas = c;
        ctx = canvas.getContext("2d");
        width = canvas.width;
        height = canvas.height;

        // Try to load drum chart for current song
        const songKey =
          bundle?.songInfo?.key || bundle?.songInfo?.songKey || null;
        if (songKey && songKey !== currentSongKey) {
          loadDrumChart(songKey);
        }
      },

      draw(bundle) {
        if (!ctx || !drumNotes.length) {
          drawNoDrumChart(ctx, width, height);
          return;
        }

        const currentTime = bundle.currentTime || 0;
        const strikeY = height * STRIKELINE_Y_RATIO;
        const laneWidth = width / (LANES.length + 0.5); // Extra half for spacing
        const pixelsPerSecond =
          ((strikeY - 50) / LOOK_AHEAD_SECONDS) * NOTE_SPEED;

        // ── Background ──
        ctx.fillStyle = HIGHWAY_BG;
        ctx.fillRect(0, 0, width, height);

        // ── Lane lines ──
        ctx.strokeStyle = LANE_LINE_COLOR;
        ctx.lineWidth = 1;
        for (let i = 0; i <= LANES.length; i++) {
          const x = laneWidth * 0.25 + i * laneWidth;
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, height);
          ctx.stroke();
        }

        // ── Lane labels at bottom ──
        ctx.font = "11px system-ui";
        ctx.textAlign = "center";
        for (let i = 0; i < LANES.length; i++) {
          const laneName = LANES[i];
          const x = laneWidth * 0.25 + (i + 0.5) * laneWidth;
          ctx.fillStyle = LANE_COLORS[laneName].fill;
          ctx.globalAlpha = 0.4;
          ctx.fillText(
            LANE_COLORS[laneName].name,
            x,
            height - 5
          );
        }
        ctx.globalAlpha = 1.0;

        // ── Strike line ──
        ctx.strokeStyle = STRIKELINE_COLOR;
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(0, strikeY);
        ctx.lineTo(width, strikeY);
        ctx.stroke();

        // ── Strike zone pads (Clone Hero style) ──
        for (let i = 0; i < LANES.length; i++) {
          const laneName = LANES[i];
          const x = laneWidth * 0.25 + (i + 0.5) * laneWidth;
          ctx.beginPath();
          ctx.arc(x, strikeY, NOTE_RADIUS + 4, 0, Math.PI * 2);
          ctx.strokeStyle = LANE_COLORS[laneName].fill + "44";
          ctx.lineWidth = 2;
          ctx.stroke();
        }

        // Kick pad
        ctx.fillStyle = LANE_COLORS.kick.fill + "22";
        ctx.fillRect(
          laneWidth * 0.25,
          strikeY + NOTE_RADIUS + 8,
          laneWidth * LANES.length,
          KICK_HEIGHT * 3
        );

        // ── Notes ──
        const windowStart = currentTime - 0.5;
        const windowEnd = currentTime + LOOK_AHEAD_SECONDS;

        for (const note of drumNotes) {
          if (note.time < windowStart || note.time > windowEnd) continue;

          const timeUntilStrike = note.time - currentTime;
          const y = strikeY - timeUntilStrike * pixelsPerSecond;

          if (y < -NOTE_RADIUS || y > height + NOTE_RADIUS) continue;

          if (note.name === "kick") {
            // Kick renders as a wide bar across all lanes
            const kickX = laneWidth * 0.25;
            const kickW = laneWidth * LANES.length;

            // Glow
            ctx.fillStyle = LANE_COLORS.kick.glow;
            ctx.fillRect(kickX - 2, y - KICK_HEIGHT - 2, kickW + 4, KICK_HEIGHT * 2 + 4);

            // Bar
            ctx.fillStyle = LANE_COLORS.kick.fill;
            ctx.fillRect(kickX, y - KICK_HEIGHT / 2, kickW, KICK_HEIGHT);

            // Bright center line
            ctx.fillStyle = "#FFAA44";
            ctx.fillRect(kickX, y - 1, kickW, 2);
          } else {
            // Lane note (circle for tom, diamond for cymbal)
            const laneIndex = LANES.indexOf(note.name);
            if (laneIndex === -1) continue;

            const x = laneWidth * 0.25 + (laneIndex + 0.5) * laneWidth;
            const color = LANE_COLORS[note.name];

            // Note approaching glow
            const glowAlpha = Math.max(0, 1 - Math.abs(timeUntilStrike) / LOOK_AHEAD_SECONDS);

            // Glow
            ctx.beginPath();
            ctx.arc(x, y, NOTE_RADIUS + 4, 0, Math.PI * 2);
            ctx.fillStyle = color.glow;
            ctx.globalAlpha = glowAlpha * 0.5;
            ctx.fill();
            ctx.globalAlpha = 1.0;

            // Note gem (circle — Clone Hero standard)
            ctx.beginPath();
            ctx.arc(x, y, NOTE_RADIUS, 0, Math.PI * 2);

            // Gradient fill (Clone Hero style)
            const grad = ctx.createRadialGradient(
              x - 4, y - 4, 2,
              x, y, NOTE_RADIUS
            );
            grad.addColorStop(0, "#FFFFFF");
            grad.addColorStop(0.3, color.fill);
            grad.addColorStop(1, color.fill + "AA");
            ctx.fillStyle = grad;
            ctx.fill();

            // Border
            ctx.strokeStyle = "#FFFFFF44";
            ctx.lineWidth = 2;
            ctx.stroke();

            // Hit/miss state rendering
            const noteIdx = drumNotes.indexOf(note);
            const state = noteStates[noteIdx];

            if (state === "hit") {
              // Hit: bright burst + shrink note
              ctx.beginPath();
              ctx.arc(x, y, NOTE_RADIUS + 15, 0, Math.PI * 2);
              ctx.fillStyle = "#FFFFFF44";
              ctx.fill();
              ctx.beginPath();
              ctx.arc(x, y, NOTE_RADIUS * 0.6, 0, Math.PI * 2);
              ctx.fillStyle = "#00FF0088";
              ctx.fill();
            } else if (state === "miss" && timeUntilStrike < 0) {
              // Miss: note goes red and fades
              ctx.globalAlpha = Math.max(0, 0.4 + timeUntilStrike);
              ctx.beginPath();
              ctx.arc(x, y, NOTE_RADIUS, 0, Math.PI * 2);
              ctx.fillStyle = "#FF000088";
              ctx.fill();
              ctx.globalAlpha = 1.0;
            } else if (Math.abs(timeUntilStrike) < 0.05) {
              // Approaching strike zone glow
              ctx.beginPath();
              ctx.arc(x, y, NOTE_RADIUS + 10, 0, Math.PI * 2);
              ctx.fillStyle = color.fill + "88";
              ctx.fill();
            }
          }
        }

        // ── Check for missed notes ──
        checkMissedNotes(currentTime);

        // ── Hit flashes on strike pads ──
        const now = performance.now();
        hitFlashes = hitFlashes.filter((f) => now - f.startTime < 300);
        for (const flash of hitFlashes) {
          const laneIndex = LANES.indexOf(flash.lane);
          if (laneIndex === -1 && flash.lane !== "kick") continue;

          const flashAlpha = 1 - (now - flash.startTime) / 300;
          ctx.globalAlpha = flashAlpha;

          if (flash.lane === "kick") {
            ctx.fillStyle = flash.type === "hit" ? "#FF880088" : "#FF000044";
            ctx.fillRect(laneWidth * 0.25, strikeY - 5, laneWidth * LANES.length, 10);
          } else {
            const fx = laneWidth * 0.25 + (laneIndex + 0.5) * laneWidth;
            ctx.beginPath();
            ctx.arc(fx, strikeY, NOTE_RADIUS + 12, 0, Math.PI * 2);
            ctx.fillStyle = flash.type === "hit" ? flash.color + "AA" : "#FF000066";
            ctx.fill();
          }
          ctx.globalAlpha = 1.0;
        }

        // ── HUD: Time ──
        ctx.fillStyle = "#FFFFFF";
        ctx.font = "14px monospace";
        ctx.textAlign = "left";
        ctx.fillText(
          `${Math.floor(currentTime / 60)}:${String(Math.floor(currentTime % 60)).padStart(2, "0")}`,
          10, 25
        );

        // ── HUD: Stats ──
        if (stats.total > 0) {
          const accuracy = Math.round((stats.hits / (stats.hits + stats.misses || 1)) * 100);
          ctx.font = "13px system-ui";
          ctx.textAlign = "left";
          ctx.fillStyle = accuracy >= 90 ? "#00FF88" : accuracy >= 70 ? "#FFFF00" : "#FF4444";
          ctx.fillText(`${accuracy}%`, 10, 45);
          ctx.fillStyle = "#FFFFFF88";
          ctx.fillText(`${stats.hits}/${stats.hits + stats.misses}`, 55, 45);

          // Streak
          if (stats.streak > 2) {
            ctx.fillStyle = "#FFD700";
            ctx.font = "bold 16px system-ui";
            ctx.textAlign = "center";
            ctx.fillText(`${stats.streak}x STREAK`, width / 2, 25);
          }
        }

        // ── HUD: MIDI status ──
        ctx.textAlign = "right";
        ctx.font = "11px system-ui";
        ctx.fillStyle = midiConnected ? "#00FF8888" : "#FF444488";
        ctx.fillText(
          midiConnected ? `MIDI: ${midiInputName}` : "No MIDI",
          width - 10, height - 10
        );

        // ── Difficulty indicator ──
        ctx.fillStyle = "#FFFFFF88";
        ctx.font = "12px system-ui";
        ctx.fillText(currentDifficulty.toUpperCase(), width - 10, 25);
      },

      resize(w, h) {
        width = w;
        height = h;
      },

      destroy() {
        ctx = null;
        canvas = null;
      },
    };
  };

  // ── MIDI Input ──

  // General MIDI drum map → Clone Hero lane mapping
  // Covers Roland, Alesis, Yamaha, and general GM standard
  const MIDI_TO_LANE = {
    // Kick drums
    36: "kick",   // Bass Drum 1 (GM)
    35: "kick",   // Acoustic Bass Drum (GM)

    // Snare
    38: "red",    // Acoustic Snare (GM)
    40: "red",    // Electric Snare (GM)
    37: "red",    // Side Stick

    // Hi-hat
    42: "yellow", // Closed Hi-Hat (GM)
    46: "yellow", // Open Hi-Hat (GM)
    44: "yellow", // Pedal Hi-Hat

    // Toms
    48: "blue",   // Hi-Mid Tom (GM)
    47: "blue",   // Low-Mid Tom (GM)
    50: "blue",   // High Tom (GM)
    45: "green",  // Low Tom (GM)
    43: "green",  // High Floor Tom (GM)
    41: "green",  // Low Floor Tom (GM)
    58: "green",  // Vibraslap (sometimes mapped to floor tom on e-kits)

    // Cymbals
    49: "yellow", // Crash Cymbal 1
    57: "yellow", // Crash Cymbal 2
    55: "blue",   // Splash Cymbal
    51: "blue",   // Ride Cymbal 1
    59: "blue",   // Ride Cymbal 2
    53: "green",  // Ride Bell
    52: "green",  // Chinese Cymbal
  };

  // Hit detection state
  const HIT_WINDOW_MS = 75;       // ±75ms tolerance (Clone Hero uses ~70ms for Expert)
  const HIT_WINDOW_SEC = HIT_WINDOW_MS / 1000;
  let noteStates = {};             // { noteIndex: "hit" | "miss" | null }
  let hitFlashes = [];             // [{ lane, time, alpha }]
  let midiConnected = false;
  let midiInputName = "";
  let stats = { hits: 0, misses: 0, total: 0, streak: 0, maxStreak: 0 };

  function resetStats() {
    stats = { hits: 0, misses: 0, total: 0, streak: 0, maxStreak: 0 };
    noteStates = {};
    hitFlashes = [];
  }

  async function initMIDI() {
    if (!navigator.requestMIDIAccess) {
      console.log("[DrumHighway] Web MIDI not supported in this browser");
      return;
    }

    try {
      const midiAccess = await navigator.requestMIDIAccess({ sysex: false });

      midiAccess.inputs.forEach((input) => {
        console.log(`[DrumHighway] MIDI input found: ${input.name}`);
        input.onmidimessage = handleMIDIMessage;
        midiConnected = true;
        midiInputName = input.name;
      });

      // Listen for new connections
      midiAccess.onstatechange = (e) => {
        if (e.port.type === "input" && e.port.state === "connected") {
          console.log(`[DrumHighway] MIDI connected: ${e.port.name}`);
          e.port.onmidimessage = handleMIDIMessage;
          midiConnected = true;
          midiInputName = e.port.name;
        } else if (e.port.type === "input" && e.port.state === "disconnected") {
          console.log(`[DrumHighway] MIDI disconnected: ${e.port.name}`);
          midiConnected = false;
          midiInputName = "";
        }
      };

      if (!midiConnected) {
        console.log("[DrumHighway] No MIDI inputs found. Connect a drum kit and refresh.");
      }
    } catch (err) {
      console.error("[DrumHighway] MIDI access denied:", err);
    }
  }

  function handleMIDIMessage(msg) {
    const [status, note, velocity] = msg.data;

    // Note On (0x90-0x9F) with velocity > 0
    if ((status & 0xF0) === 0x90 && velocity > 0) {
      const lane = MIDI_TO_LANE[note];
      if (!lane) return; // Unmapped MIDI note, ignore

      const hitTime = getCurrentPlaybackTime();
      if (hitTime === null) return;

      // Find the closest chart note in this lane within the hit window
      const result = findClosestNote(lane, hitTime);

      if (result) {
        // HIT
        noteStates[result.index] = "hit";
        stats.hits++;
        stats.streak++;
        if (stats.streak > stats.maxStreak) stats.maxStreak = stats.streak;

        // Visual flash
        hitFlashes.push({
          lane,
          startTime: performance.now(),
          color: LANE_COLORS[lane]?.fill || "#FFFFFF",
          type: "hit",
        });
      } else {
        // No matching note — overhit / ghost note
        hitFlashes.push({
          lane,
          startTime: performance.now(),
          color: "#FF000088",
          type: "overhit",
        });
      }

      stats.total++;
    }
  }

  function getCurrentPlaybackTime() {
    // Try to get current playback time from SlopSmith's audio element
    const audio = document.querySelector("audio") || document.getElementById("main-audio");
    if (audio && !audio.paused) return audio.currentTime;
    return null;
  }

  function findClosestNote(lane, currentTime) {
    if (!drumNotes.length) return null;

    let closest = null;
    let closestDist = Infinity;

    for (let i = 0; i < drumNotes.length; i++) {
      const note = drumNotes[i];
      if (note.name !== lane) continue;
      if (noteStates[i] === "hit") continue; // Already hit

      const dist = Math.abs(note.time - currentTime);
      if (dist < HIT_WINDOW_SEC && dist < closestDist) {
        closest = { index: i, note, dist };
        closestDist = dist;
      }
    }

    return closest;
  }

  // Check for missed notes (passed the strike line without being hit)
  function checkMissedNotes(currentTime) {
    for (let i = 0; i < drumNotes.length; i++) {
      if (noteStates[i]) continue; // Already judged
      const note = drumNotes[i];
      if (note.time < currentTime - HIT_WINDOW_SEC) {
        noteStates[i] = "miss";
        stats.misses++;
        stats.streak = 0;
      }
    }
  }

  // Initialize MIDI on plugin load
  initMIDI();

  function drawNoDrumChart(ctx, w, h) {
    ctx.fillStyle = HIGHWAY_BG;
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "#FFFFFF44";
    ctx.font = "18px system-ui";
    ctx.textAlign = "center";
    ctx.fillText("No drum chart loaded", w / 2, h / 2 - 10);
    ctx.font = "13px system-ui";
    ctx.fillText(
      "Combine a Clone Hero chart with this song",
      w / 2,
      h / 2 + 15
    );
  }

  // ── Hook into song changes ──
  const originalPlaySong = window.playSong;
  if (originalPlaySong) {
    window.playSong = async function (...args) {
      const result = await originalPlaySong.apply(this, args);
      const songKey = args[0]?.key || args[0]?.songKey || args[0]?.name || null;
      if (songKey) loadDrumChart(songKey);
      return result;
    };
  }

  // Difficulty switching via keyboard
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

    const diffMap = {
      "1": "easy",
      "2": "medium",
      "3": "hard",
      "4": "expert",
    };
    if (e.key in diffMap) {
      currentDifficulty = diffMap[e.key];
      if (currentSongKey) loadDrumChart(currentSongKey);
      console.log(`[DrumHighway] Difficulty: ${currentDifficulty}`);
    }
  });

  console.log("[DrumHighway] Plugin loaded. Difficulty keys: 1-Easy 2-Med 3-Hard 4-Expert");
})();
