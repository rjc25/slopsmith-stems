/**
 * Vocal Highway — YARC/Rock Band-style vocal visualization with pitch detection.
 *
 * Features:
 *   - Horizontal scrolling lyrics at top of screen
 *   - Pitch tubes (colored bars) showing target pitch over time
 *   - Real-time pitch detection from microphone via Web Audio API
 *   - Current pitch indicator showing where the singer IS vs target
 *   - Scoring: detected pitch compared to target within +/-1 semitone tolerance
 *   - Visual feedback: tubes turn green when on-pitch, red when off
 *
 * Pitch detection uses autocorrelation in the time domain for low latency.
 */

(function () {
  "use strict";

  // ── Visual Constants ──
  var BG_COLOR = "#0d0d1a";
  var TUBE_COLOR = "#4488FF";
  var TUBE_HIT_COLOR = "#44FF88";
  var TUBE_MISS_COLOR = "#FF4444";
  var LYRICS_COLOR = "#FFFFFF";
  var LYRICS_PAST_COLOR = "#FFFFFF44";
  var LYRICS_ACTIVE_COLOR = "#44DDFF";
  var PITCH_INDICATOR_COLOR = "#FFAA00";
  var GUIDE_LINE_COLOR = "#FFFFFF11";

  var LOOK_AHEAD_SEC = 3.0;
  var LOOK_BEHIND_SEC = 0.5;
  var LYRICS_AREA_HEIGHT = 50;     // px reserved at top for lyrics
  var PITCH_MIN = 36;              // C2 — low end of vocal range
  var PITCH_MAX = 84;              // C6 — high end of vocal range
  var PITCH_RANGE = PITCH_MAX - PITCH_MIN;
  var SEMITONE_TOLERANCE = 1.0;    // +/-1 semitone for a "hit"

  // ── State ──
  var vocalEvents = [];      // [{time, duration, pitch, lyric}, ...]
  var currentSongKey = null;
  var audioContext = null;
  var analyserNode = null;
  var micStream = null;
  var micActive = false;
  var detectedPitch = null;  // Current detected MIDI note (float)
  var detectedConfidence = 0;
  var autocorrBuffer = null;

  // Scoring
  var stats = { hits: 0, misses: 0, total: 0, streak: 0, maxStreak: 0 };
  var phraseScores = {};     // { eventIndex: "hit" | "miss" }

  // ── Vocal Chart Loading ──

  async function loadVocalChart(songKey) {
    try {
      var resp = await fetch(
        "/api/plugins/vocal_highway/chart/" + encodeURIComponent(songKey)
      );
      if (!resp.ok) return false;

      var data = await resp.json();
      if (!data.vocals || !data.vocals.length) return false;

      vocalEvents = data.vocals;
      currentSongKey = songKey;
      stats = { hits: 0, misses: 0, total: 0, streak: 0, maxStreak: 0 };
      phraseScores = {};

      console.log(
        "[VocalHighway] Loaded " + vocalEvents.length + " vocal events for " + songKey
      );
      return true;
    } catch (err) {
      console.error("[VocalHighway] Failed to load chart:", err);
      return false;
    }
  }

  // ── Microphone & Pitch Detection ──

  async function initMicrophone() {
    if (micActive) return true;

    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });

      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      var source = audioContext.createMediaStreamSource(micStream);

      analyserNode = audioContext.createAnalyser();
      analyserNode.fftSize = 2048;
      source.connect(analyserNode);

      autocorrBuffer = new Float32Array(analyserNode.fftSize);
      micActive = true;

      console.log("[VocalHighway] Microphone active, sample rate: " + audioContext.sampleRate);
      return true;
    } catch (err) {
      console.error("[VocalHighway] Microphone access denied:", err);
      micActive = false;
      return false;
    }
  }

  function stopMicrophone() {
    if (micStream) {
      micStream.getTracks().forEach(function (t) { t.stop(); });
      micStream = null;
    }
    if (audioContext && audioContext.state !== "closed") {
      audioContext.close();
      audioContext = null;
    }
    analyserNode = null;
    micActive = false;
  }

  /**
   * Autocorrelation-based pitch detection.
   *
   * Operates in the time domain for low latency. Finds the fundamental
   * frequency by looking for the first strong peak in the autocorrelation
   * function of the audio signal.
   */
  function detectPitch() {
    if (!analyserNode || !autocorrBuffer) {
      detectedPitch = null;
      detectedConfidence = 0;
      return;
    }

    analyserNode.getFloatTimeDomainData(autocorrBuffer);
    var buf = autocorrBuffer;
    var n = buf.length;
    var sampleRate = audioContext.sampleRate;

    // Check RMS — if signal is too quiet, skip detection
    var rms = 0;
    for (var i = 0; i < n; i++) {
      rms += buf[i] * buf[i];
    }
    rms = Math.sqrt(rms / n);

    if (rms < 0.01) {
      detectedPitch = null;
      detectedConfidence = 0;
      return;
    }

    // Autocorrelation
    // We look for the period of the fundamental frequency.
    // Search range: ~55 Hz (A1) to ~1500 Hz (F#6)
    var minPeriod = Math.floor(sampleRate / 1500);
    var maxPeriod = Math.floor(sampleRate / 55);
    maxPeriod = Math.min(maxPeriod, n - 1);

    // Normalized autocorrelation with cumulative mean normalization (YIN-like)
    var bestPeriod = -1;
    var bestCorrelation = 0;

    for (var tau = minPeriod; tau <= maxPeriod; tau++) {
      var corr = 0;
      var norm1 = 0;
      var norm2 = 0;
      var windowSize = n - tau;

      for (var j = 0; j < windowSize; j++) {
        corr += buf[j] * buf[j + tau];
        norm1 += buf[j] * buf[j];
        norm2 += buf[j + tau] * buf[j + tau];
      }

      var normFactor = Math.sqrt(norm1 * norm2);
      if (normFactor < 1e-8) continue;

      var normalized = corr / normFactor;

      if (normalized > bestCorrelation) {
        bestCorrelation = normalized;
        bestPeriod = tau;
      }
    }

    if (bestPeriod > 0 && bestCorrelation > 0.7) {
      // Parabolic interpolation for sub-sample accuracy
      var freq = sampleRate / bestPeriod;
      // Convert frequency to MIDI note number
      detectedPitch = 12 * Math.log2(freq / 440) + 69;
      detectedConfidence = bestCorrelation;
    } else {
      detectedPitch = null;
      detectedConfidence = 0;
    }
  }

  // ── Scoring ──

  function scorePitch(targetPitch, detected) {
    if (detected === null || targetPitch === null || targetPitch === 0) {
      return "silent";
    }
    var diff = Math.abs(detected - targetPitch);
    // Allow octave equivalence (singer can be an octave off)
    var diffOctave = diff % 12;
    if (diffOctave > 6) diffOctave = 12 - diffOctave;

    if (diffOctave <= SEMITONE_TOLERANCE) return "hit";
    if (diffOctave <= SEMITONE_TOLERANCE * 2) return "close";
    return "miss";
  }

  // ── Helper: MIDI note to display name ──

  function midiNoteName(note) {
    var names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
    var octave = Math.floor(note / 12) - 1;
    return names[Math.round(note) % 12] + octave;
  }

  // ── Renderer (SlopSmith viz contract) ──

  window.slopsmithViz_vocal_highway = function () {
    var ctx = null;
    var canvas = null;
    var width = 0;
    var height = 0;

    return {
      contextType: "2d",

      init: function (c, bundle) {
        canvas = c;
        ctx = canvas.getContext("2d");
        width = canvas.width;
        height = canvas.height;

        // Load vocal chart if we have a song
        var songKey = bundle && bundle.songInfo
          ? (bundle.songInfo.key || bundle.songInfo.songKey || null)
          : null;
        if (songKey && songKey !== currentSongKey) {
          loadVocalChart(songKey);
        }

        // Initialize microphone
        initMicrophone();

        // Auto-mute vocals stem when this plugin loads
        if (window.slopsmith) {
          window.slopsmith.emit("stems:mute", { stem: "vocals" });
        }
      },

      draw: function (bundle) {
        if (!ctx) return;

        var currentTime = (bundle && bundle.currentTime) || 0;
        var pitchAreaTop = LYRICS_AREA_HEIGHT;
        var pitchAreaHeight = height - LYRICS_AREA_HEIGHT - 30; // 30px for bottom HUD

        // Run pitch detection each frame
        detectPitch();

        // ── Background ──
        ctx.fillStyle = BG_COLOR;
        ctx.fillRect(0, 0, width, height);

        if (!vocalEvents.length) {
          drawNoVocalChart(ctx, width, height);
          return;
        }

        // ── Pitch guide lines (every 2 semitones) ──
        ctx.strokeStyle = GUIDE_LINE_COLOR;
        ctx.lineWidth = 1;
        for (var note = PITCH_MIN; note <= PITCH_MAX; note += 2) {
          var noteY = pitchToY(note, pitchAreaTop, pitchAreaHeight);
          ctx.beginPath();
          ctx.moveTo(0, noteY);
          ctx.lineTo(width, noteY);
          ctx.stroke();
        }

        // Pitch axis labels (every octave)
        ctx.font = "10px monospace";
        ctx.fillStyle = "#FFFFFF33";
        ctx.textAlign = "left";
        for (var n = PITCH_MIN; n <= PITCH_MAX; n += 12) {
          var ny = pitchToY(n, pitchAreaTop, pitchAreaHeight);
          ctx.fillText(midiNoteName(n), 4, ny + 3);
        }

        // ── Time window ──
        var windowStart = currentTime - LOOK_BEHIND_SEC;
        var windowEnd = currentTime + LOOK_AHEAD_SEC;
        var totalWindow = LOOK_AHEAD_SEC + LOOK_BEHIND_SEC;
        var pixelsPerSecond = width / totalWindow;
        // Current time position on screen (where the "now" line is)
        var nowX = (LOOK_BEHIND_SEC / totalWindow) * width;

        // ── "Now" line ──
        ctx.strokeStyle = "#FFFFFF44";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(nowX, pitchAreaTop);
        ctx.lineTo(nowX, pitchAreaTop + pitchAreaHeight);
        ctx.stroke();

        // ── Render pitch tubes ──
        var activeLyric = null;
        var activeEventResult = null;

        for (var i = 0; i < vocalEvents.length; i++) {
          var ev = vocalEvents[i];
          var evEnd = ev.time + (ev.duration || 0.1);

          // Skip events outside the visible window
          if (evEnd < windowStart || ev.time > windowEnd) continue;

          // X positions on screen
          var x1 = nowX + (ev.time - currentTime) * pixelsPerSecond;
          var x2 = nowX + (evEnd - currentTime) * pixelsPerSecond;
          var tubeWidth = Math.max(x2 - x1, 3);

          // Y position based on pitch
          var pitch = ev.pitch || 0;
          if (pitch < PITCH_MIN || pitch > PITCH_MAX) continue;
          var tubeY = pitchToY(pitch, pitchAreaTop, pitchAreaHeight);
          var tubeHeight = Math.max(pitchAreaHeight / PITCH_RANGE * 1.5, 6);

          // Determine tube color based on scoring
          var tubeColor = TUBE_COLOR;
          var isActive = currentTime >= ev.time && currentTime <= evEnd;

          if (isActive && detectedPitch !== null) {
            var result = scorePitch(pitch, detectedPitch);
            activeEventResult = result;

            if (result === "hit") {
              tubeColor = TUBE_HIT_COLOR;
              if (!phraseScores[i]) {
                phraseScores[i] = "hit";
                stats.hits++;
                stats.total++;
                stats.streak++;
                if (stats.streak > stats.maxStreak) stats.maxStreak = stats.streak;
              }
            } else if (result === "close") {
              tubeColor = "#AACC44";
            } else {
              tubeColor = TUBE_MISS_COLOR;
              if (!phraseScores[i] && currentTime > ev.time + 0.3) {
                phraseScores[i] = "miss";
                stats.misses++;
                stats.total++;
                stats.streak = 0;
              }
            }
          } else if (phraseScores[i] === "hit") {
            tubeColor = TUBE_HIT_COLOR + "88";
          } else if (phraseScores[i] === "miss") {
            tubeColor = TUBE_MISS_COLOR + "44";
          }

          // Past events dim out
          if (evEnd < currentTime) {
            ctx.globalAlpha = 0.35;
          }

          // Draw the pitch tube
          ctx.fillStyle = tubeColor;
          var radius = tubeHeight / 2;
          drawRoundedRect(ctx, x1, tubeY - radius, tubeWidth, tubeHeight, radius);
          ctx.fill();

          // Tube glow for active event
          if (isActive) {
            ctx.shadowColor = tubeColor;
            ctx.shadowBlur = 12;
            ctx.fillStyle = tubeColor + "66";
            drawRoundedRect(ctx, x1 - 2, tubeY - radius - 2, tubeWidth + 4, tubeHeight + 4, radius + 2);
            ctx.fill();
            ctx.shadowBlur = 0;
          }

          ctx.globalAlpha = 1.0;

          // Track active lyric for display
          if (isActive && ev.lyric) {
            activeLyric = ev;
          }
        }

        // ── Detected pitch indicator ──
        if (micActive && detectedPitch !== null && detectedConfidence > 0.5) {
          var indicatorY = pitchToY(detectedPitch, pitchAreaTop, pitchAreaHeight);

          // Clamp to visible area
          indicatorY = Math.max(pitchAreaTop + 5, Math.min(indicatorY, pitchAreaTop + pitchAreaHeight - 5));

          // Color based on scoring result
          var indicatorColor = PITCH_INDICATOR_COLOR;
          if (activeEventResult === "hit") indicatorColor = TUBE_HIT_COLOR;
          else if (activeEventResult === "miss") indicatorColor = TUBE_MISS_COLOR;

          // Draw circle indicator at the "now" line
          ctx.beginPath();
          ctx.arc(nowX, indicatorY, 8, 0, Math.PI * 2);
          ctx.fillStyle = indicatorColor;
          ctx.fill();
          ctx.strokeStyle = "#FFFFFF";
          ctx.lineWidth = 2;
          ctx.stroke();

          // Horizontal line across the tube area
          ctx.strokeStyle = indicatorColor + "44";
          ctx.lineWidth = 1;
          ctx.setLineDash([4, 4]);
          ctx.beginPath();
          ctx.moveTo(nowX - 40, indicatorY);
          ctx.lineTo(nowX + 40, indicatorY);
          ctx.stroke();
          ctx.setLineDash([]);

          // Note name label
          ctx.font = "11px monospace";
          ctx.fillStyle = indicatorColor;
          ctx.textAlign = "right";
          ctx.fillText(midiNoteName(detectedPitch), nowX - 14, indicatorY + 4);
        }

        // ── Scrolling Lyrics (top area) ──
        drawLyrics(ctx, currentTime, width, pixelsPerSecond, nowX, activeLyric);

        // ── HUD: Stats ──
        ctx.textAlign = "left";
        var hudY = height - 10;
        ctx.font = "13px system-ui";

        if (stats.total > 0) {
          var accuracy = Math.round((stats.hits / (stats.hits + stats.misses || 1)) * 100);
          ctx.fillStyle = accuracy >= 90 ? "#00FF88" : accuracy >= 70 ? "#FFFF00" : "#FF4444";
          ctx.fillText(accuracy + "%", 10, hudY);
          ctx.fillStyle = "#FFFFFF88";
          ctx.fillText(stats.hits + "/" + (stats.hits + stats.misses), 55, hudY);
        }

        // Streak
        if (stats.streak > 2) {
          ctx.fillStyle = "#FFD700";
          ctx.font = "bold 15px system-ui";
          ctx.textAlign = "center";
          ctx.fillText(stats.streak + "x STREAK", width / 2, hudY);
        }

        // Mic status
        ctx.textAlign = "right";
        ctx.font = "11px system-ui";
        ctx.fillStyle = micActive ? "#00FF8888" : "#FF444488";
        ctx.fillText(micActive ? "MIC: Active" : "No Mic", width - 10, hudY);

        // Time
        ctx.textAlign = "left";
        ctx.fillStyle = "#FFFFFF";
        ctx.font = "14px monospace";
        var mins = Math.floor(currentTime / 60);
        var secs = String(Math.floor(currentTime % 60)).padStart(2, "0");
        ctx.fillText(mins + ":" + secs, 10, 25);
      },

      resize: function (w, h) {
        width = w;
        height = h;
      },

      destroy: function () {
        stopMicrophone();
        // Unmute vocals on exit
        if (window.slopsmith) {
          window.slopsmith.emit("stems:unmute", { stem: "vocals" });
        }
        ctx = null;
        canvas = null;
      },
    };
  };

  // ── Lyrics Renderer ──

  function drawLyrics(ctx, currentTime, width, pxPerSec, nowX, activeLyric) {
    ctx.save();

    // Background band for lyrics
    ctx.fillStyle = "#000000AA";
    ctx.fillRect(0, 0, width, LYRICS_AREA_HEIGHT);

    // Bottom border
    ctx.strokeStyle = "#FFFFFF22";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, LYRICS_AREA_HEIGHT);
    ctx.lineTo(width, LYRICS_AREA_HEIGHT);
    ctx.stroke();

    // Gather visible lyrics in time window
    var windowStart = currentTime - 2;
    var windowEnd = currentTime + LOOK_AHEAD_SEC + 2;

    ctx.font = "bold 18px system-ui";
    ctx.textBaseline = "middle";

    for (var i = 0; i < vocalEvents.length; i++) {
      var ev = vocalEvents[i];
      if (!ev.lyric) continue;
      if (ev.time > windowEnd) break;

      var evEnd = ev.time + (ev.duration || 0.1);
      if (evEnd < windowStart) continue;

      var x = nowX + (ev.time - currentTime) * pxPerSec;
      var isActive = currentTime >= ev.time && currentTime <= evEnd;
      var isPast = evEnd < currentTime;

      if (isActive) {
        ctx.fillStyle = LYRICS_ACTIVE_COLOR;
        ctx.font = "bold 20px system-ui";
      } else if (isPast) {
        ctx.fillStyle = LYRICS_PAST_COLOR;
        ctx.font = "bold 18px system-ui";
      } else {
        ctx.fillStyle = LYRICS_COLOR;
        ctx.font = "bold 18px system-ui";
      }

      ctx.textAlign = "left";

      // Clean up lyric text (remove + for extending notes, - for hyphens)
      var text = ev.lyric.replace(/^\+$/, "~").replace(/^-/, "");
      if (text === "~" || text === "") continue;

      ctx.fillText(text, x, LYRICS_AREA_HEIGHT / 2);
    }

    ctx.restore();
  }

  // ── Helpers ──

  function pitchToY(pitch, areaTop, areaHeight) {
    // Higher pitch = higher on screen (lower Y)
    var normalized = (pitch - PITCH_MIN) / PITCH_RANGE;
    return areaTop + areaHeight - (normalized * areaHeight);
  }

  function drawRoundedRect(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  function drawNoVocalChart(ctx, w, h) {
    ctx.fillStyle = BG_COLOR;
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "#FFFFFF44";
    ctx.font = "18px system-ui";
    ctx.textAlign = "center";
    ctx.fillText("No vocal chart loaded", w / 2, h / 2 - 10);
    ctx.font = "13px system-ui";
    ctx.fillText(
      "Combine a Clone Hero chart with vocal data",
      w / 2,
      h / 2 + 15
    );

    if (!micActive) {
      ctx.fillStyle = "#FFAA0088";
      ctx.font = "12px system-ui";
      ctx.fillText("Click to enable microphone", w / 2, h / 2 + 40);
    }
  }

  // ── Hook into SlopSmith ──

  var originalPlaySong = window.playSong;
  if (originalPlaySong) {
    window.playSong = async function () {
      var result = await originalPlaySong.apply(this, arguments);
      var songKey =
        arguments[0] && (arguments[0].key || arguments[0].songKey || arguments[0].name) || null;
      if (songKey) loadVocalChart(songKey);
      return result;
    };
  }

  if (window.slopsmith) {
    window.slopsmith.on("song:play", function (data) {
      if (data && data.songKey) loadVocalChart(data.songKey);
    });

    window.slopsmith.on("song:stop", function () {
      vocalEvents = [];
      currentSongKey = null;
      stats = { hits: 0, misses: 0, total: 0, streak: 0, maxStreak: 0 };
      phraseScores = {};
    });
  }

  console.log("[VocalHighway] Plugin loaded. Microphone pitch detection active.");
})();
