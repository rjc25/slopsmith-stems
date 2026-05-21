/**
 * Stem Toggle Plugin — Real-time instrument stem control during playback.
 *
 * Loads individual stems (drums, bass, vocals, guitar, other) as separate
 * Audio elements synced to the main playback. Keyboard shortcuts toggle
 * each stem on/off with a smooth crossfade.
 *
 * Shortcuts:
 *   G — Toggle guitar
 *   V — Toggle vocals
 *   B — Toggle bass
 *   D — Toggle drums
 *   O — Toggle other
 *   A — All stems on (reset)
 *   S — Solo mode (only hear the instrument you're playing)
 */

(function () {
  "use strict";

  const STEMS = ["drums", "bass", "vocals", "guitar", "other"];
  const FADE_MS = 150; // Crossfade duration for smooth toggle
  const SYNC_TOLERANCE = 0.05; // Max drift in seconds before resync

  const SHORTCUTS = {
    g: "guitar",
    v: "vocals",
    b: "bass",
    d: "drums",
    o: "other",
  };

  // State
  let stemAudios = {}; // { name: HTMLAudioElement }
  let stemStates = {}; // { name: { active: true, volume: 1.0 } }
  let currentSongKey = null;
  let mainAudio = null;
  let isActive = false;
  let soloStem = null;
  let uiContainer = null;

  // ── Stem Audio Management ──

  function createStemAudio(url, name) {
    const audio = new Audio(url);
    audio.preload = "auto";
    audio.volume = 1.0;
    audio.crossOrigin = "anonymous";
    return audio;
  }

  async function loadStems(songKey) {
    // Cleanup previous stems
    unloadStems();

    try {
      const resp = await fetch(
        `/api/plugins/stem_toggle/stems/${encodeURIComponent(songKey)}`
      );
      const data = await resp.json();

      if (!data.has_stems) {
        console.log("[StemToggle] No stems available for this song");
        hideUI();
        return false;
      }

      for (const stem of data.stems) {
        const audio = createStemAudio(stem.url, stem.name);
        stemAudios[stem.name] = audio;
        stemStates[stem.name] = { active: true, volume: 1.0 };
      }

      currentSongKey = songKey;
      isActive = true;
      showUI();
      console.log(
        `[StemToggle] Loaded ${data.stems.length} stems for ${songKey}`
      );
      return true;
    } catch (err) {
      console.error("[StemToggle] Failed to load stems:", err);
      return false;
    }
  }

  function unloadStems() {
    for (const audio of Object.values(stemAudios)) {
      audio.pause();
      audio.src = "";
    }
    stemAudios = {};
    stemStates = {};
    currentSongKey = null;
    isActive = false;
    soloStem = null;
  }

  // ── Playback Sync ──

  function syncStems() {
    if (!mainAudio || !isActive) return;

    const mainTime = mainAudio.currentTime;
    const mainPaused = mainAudio.paused;

    for (const [name, audio] of Object.entries(stemAudios)) {
      const state = stemStates[name];
      if (!state) continue;

      // Sync time if drifted
      if (Math.abs(audio.currentTime - mainTime) > SYNC_TOLERANCE) {
        audio.currentTime = mainTime;
      }

      // Sync play/pause state
      if (mainPaused && !audio.paused) {
        audio.pause();
      } else if (!mainPaused && audio.paused && state.active) {
        audio.play().catch(() => {});
      }

      // Apply volume
      audio.volume = state.active ? state.volume : 0;
    }
  }

  // Start sync loop
  let syncInterval = null;
  function startSyncLoop() {
    if (syncInterval) return;
    syncInterval = setInterval(syncStems, 100);
  }

  function stopSyncLoop() {
    if (syncInterval) {
      clearInterval(syncInterval);
      syncInterval = null;
    }
  }

  // ── Toggle Logic ──

  function toggleStem(name) {
    if (!stemStates[name]) return;

    soloStem = null; // Exit solo mode on any toggle
    const state = stemStates[name];
    state.active = !state.active;

    // Smooth crossfade
    const audio = stemAudios[name];
    if (audio) {
      fadeVolume(audio, state.active ? 1.0 : 0, FADE_MS);
    }

    updateUI();
    console.log(`[StemToggle] ${name}: ${state.active ? "ON" : "OFF"}`);
  }

  function allStemsOn() {
    soloStem = null;
    for (const name of STEMS) {
      if (stemStates[name]) {
        stemStates[name].active = true;
        const audio = stemAudios[name];
        if (audio) fadeVolume(audio, 1.0, FADE_MS);
      }
    }
    updateUI();
    console.log("[StemToggle] All stems ON");
  }

  function soloMode(stemName) {
    if (soloStem === stemName) {
      // Toggle solo off → all on
      allStemsOn();
      return;
    }

    soloStem = stemName;
    for (const name of STEMS) {
      if (!stemStates[name]) continue;
      const shouldPlay = name === stemName;
      stemStates[name].active = shouldPlay;
      const audio = stemAudios[name];
      if (audio) fadeVolume(audio, shouldPlay ? 1.0 : 0, FADE_MS);
    }
    updateUI();
    console.log(`[StemToggle] Solo: ${stemName}`);
  }

  function fadeVolume(audio, targetVol, durationMs) {
    const startVol = audio.volume;
    const startTime = performance.now();
    const step = () => {
      const elapsed = performance.now() - startTime;
      const progress = Math.min(elapsed / durationMs, 1);
      audio.volume = startVol + (targetVol - startVol) * progress;
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // ── UI ──

  function createUI() {
    if (uiContainer) return;

    uiContainer = document.createElement("div");
    uiContainer.id = "stem-toggle-ui";
    uiContainer.style.cssText = `
      position: fixed;
      bottom: 80px;
      left: 50%;
      transform: translateX(-50%);
      display: flex;
      gap: 8px;
      padding: 8px 16px;
      background: rgba(0, 0, 0, 0.85);
      border-radius: 12px;
      z-index: 9999;
      font-family: system-ui, -apple-system, sans-serif;
      font-size: 13px;
      backdrop-filter: blur(10px);
      border: 1px solid rgba(255, 255, 255, 0.1);
      transition: opacity 0.3s;
      user-select: none;
    `;

    const stemIcons = {
      drums: "🥁",
      bass: "🎸",
      vocals: "🎤",
      guitar: "🎵",
      other: "🎹",
    };

    const stemKeys = {
      drums: "D",
      bass: "B",
      vocals: "V",
      guitar: "G",
      other: "O",
    };

    for (const name of STEMS) {
      const btn = document.createElement("button");
      btn.id = `stem-btn-${name}`;
      btn.title = `Toggle ${name} (${stemKeys[name]})`;
      btn.innerHTML = `${stemIcons[name]} <span style="font-size:10px;opacity:0.6">${stemKeys[name]}</span>`;
      btn.style.cssText = `
        padding: 6px 12px;
        border-radius: 8px;
        border: 1px solid rgba(255, 255, 255, 0.2);
        background: rgba(255, 255, 255, 0.15);
        color: white;
        cursor: pointer;
        transition: all 0.15s;
        font-size: 16px;
        min-width: 48px;
        text-align: center;
      `;
      btn.addEventListener("click", () => toggleStem(name));
      btn.addEventListener("dblclick", () => soloMode(name));
      uiContainer.appendChild(btn);
    }

    // Reset button
    const resetBtn = document.createElement("button");
    resetBtn.title = "All stems on (A)";
    resetBtn.textContent = "ALL";
    resetBtn.style.cssText = `
      padding: 6px 12px;
      border-radius: 8px;
      border: 1px solid rgba(100, 200, 100, 0.4);
      background: rgba(100, 200, 100, 0.2);
      color: #8f8;
      cursor: pointer;
      font-size: 11px;
      font-weight: bold;
    `;
    resetBtn.addEventListener("click", allStemsOn);
    uiContainer.appendChild(resetBtn);

    document.body.appendChild(uiContainer);
  }

  function updateUI() {
    for (const name of STEMS) {
      const btn = document.getElementById(`stem-btn-${name}`);
      if (!btn || !stemStates[name]) continue;

      const active = stemStates[name].active;
      const isSolo = soloStem === name;

      btn.style.background = isSolo
        ? "rgba(100, 200, 255, 0.4)"
        : active
          ? "rgba(255, 255, 255, 0.15)"
          : "rgba(255, 50, 50, 0.3)";
      btn.style.opacity = active ? "1" : "0.4";
      btn.style.border = isSolo
        ? "1px solid rgba(100, 200, 255, 0.6)"
        : active
          ? "1px solid rgba(255, 255, 255, 0.2)"
          : "1px solid rgba(255, 50, 50, 0.4)";
    }
  }

  function showUI() {
    createUI();
    if (uiContainer) uiContainer.style.display = "flex";
    updateUI();
  }

  function hideUI() {
    if (uiContainer) uiContainer.style.display = "none";
  }

  // ── Keyboard Handler ──

  function onKeyDown(e) {
    if (!isActive) return;
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

    const key = e.key.toLowerCase();

    if (key in SHORTCUTS) {
      e.preventDefault();
      toggleStem(SHORTCUTS[key]);
    } else if (key === "a") {
      e.preventDefault();
      allStemsOn();
    } else if (key === "s") {
      e.preventDefault();
      // Solo the guitar stem (most common use case)
      soloMode("guitar");
    }
  }

  // ── Hook into SlopSmith ──

  // Wrap playSong to load stems when a song starts
  const originalPlaySong = window.playSong;
  if (originalPlaySong) {
    window.playSong = async function (...args) {
      const result = await originalPlaySong.apply(this, args);

      // Try to find the main audio element
      mainAudio =
        document.querySelector("audio") ||
        document.getElementById("main-audio");

      // Extract song key from args or current state
      const songKey =
        args[0]?.key ||
        args[0]?.songKey ||
        args[0]?.name ||
        (typeof args[0] === "string" ? args[0] : null);

      if (songKey) {
        const loaded = await loadStems(songKey);
        if (loaded) {
          startSyncLoop();

          // Mute the main audio's guitar track if stems are loaded
          // (the stems replace the original mix)
          if (mainAudio) {
            // We keep main audio for sync reference but mute it
            // since stems provide the audio now
            mainAudio.volume = 0;
          }
        }
      }

      return result;
    };
  }

  // Listen for SlopSmith events
  if (window.slopsmith) {
    window.slopsmith.on("song:play", (data) => {
      mainAudio =
        document.querySelector("audio") ||
        document.getElementById("main-audio");
      if (data?.songKey) loadStems(data.songKey);
    });

    window.slopsmith.on("song:stop", () => {
      stopSyncLoop();
      unloadStems();
      hideUI();
    });
  }

  // Register keyboard handler
  document.addEventListener("keydown", onKeyDown);

  // Cleanup on page unload
  window.addEventListener("beforeunload", () => {
    stopSyncLoop();
    unloadStems();
  });

  console.log("[StemToggle] Plugin loaded. Shortcuts: G V B D O A S");
})();
