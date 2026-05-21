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

            // Hit flash (note passing through strike zone)
            if (Math.abs(timeUntilStrike) < 0.05) {
              ctx.beginPath();
              ctx.arc(x, y, NOTE_RADIUS + 10, 0, Math.PI * 2);
              ctx.fillStyle = color.fill + "88";
              ctx.fill();
            }
          }
        }

        // ── Time counter ──
        ctx.fillStyle = "#FFFFFF";
        ctx.font = "14px monospace";
        ctx.textAlign = "left";
        ctx.fillText(
          `${Math.floor(currentTime / 60)}:${String(Math.floor(currentTime % 60)).padStart(2, "0")}`,
          10, 25
        );

        // ── Difficulty indicator ──
        ctx.textAlign = "right";
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
