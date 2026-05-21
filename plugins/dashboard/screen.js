/**
 * Stem Manager Dashboard — Frontend Logic
 *
 * Handles tab switching, library data, processing queue with polling,
 * combine pairing, multiplayer room management, and settings.
 * Pure vanilla JS — no frameworks.
 */

(function () {
  "use strict";

  // ── State ──

  var library = {};       // { songs: {...}, settings: {...} }
  var currentFilter = "all";
  var selectedSongs = {};  // { key: true }
  var pollInterval = null;
  var combineSelection = { cdlc: null, ch: null };
  var pendingMatches = [];  // auto-match results

  // ── API Helpers ──

  function api(method, path, body) {
    var opts = {
      method: method,
      headers: { "Content-Type": "application/json" },
    };
    if (body) opts.body = JSON.stringify(body);
    return fetch("/api/plugins/dashboard" + path, opts).then(function (r) {
      if (!r.ok) {
        return r.json().then(function (err) {
          throw new Error(err.detail || err.message || "Request failed");
        });
      }
      return r.json();
    });
  }

  // ── Tab Switching ──

  function switchTab(tabName) {
    // Hide all content
    var contents = document.querySelectorAll(".dash-tab-content");
    for (var i = 0; i < contents.length; i++) {
      contents[i].style.display = "none";
    }

    // Deactivate all tabs
    var tabs = document.querySelectorAll(".dash-tab");
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].style.borderBottomColor = "transparent";
      tabs[i].style.color = "#8888aa";
    }

    // Activate selected
    var target = document.getElementById("tab-" + tabName);
    if (target) target.style.display = "block";

    var activeTab = document.querySelector('.dash-tab[data-tab="' + tabName + '"]');
    if (activeTab) {
      activeTab.style.borderBottomColor = "#6c63ff";
      activeTab.style.color = "#fff";
    }

    // Refresh data for the tab
    if (tabName === "library") loadLibrary();
    if (tabName === "process") { loadSettings(); checkProcessStatus(); }
    if (tabName === "combine") loadCombineData();
    if (tabName === "multiplayer") refreshRooms();
  }

  // ── Library ──

  function loadLibrary() {
    api("GET", "/library").then(function (data) {
      library = data;
      renderLibrary();
      updateStats();
    }).catch(function (err) {
      console.error("Failed to load library:", err);
    });
  }

  function renderLibrary() {
    var container = document.getElementById("library-rows");
    var songs = library.songs || {};
    var keys = Object.keys(songs);
    var search = (document.getElementById("library-search").value || "").toLowerCase();

    // Filter
    var filtered = keys.filter(function (key) {
      var s = songs[key];
      var matchesSearch = !search ||
        (s.title || "").toLowerCase().indexOf(search) >= 0 ||
        (s.artist || "").toLowerCase().indexOf(search) >= 0;

      if (!matchesSearch) return false;

      if (currentFilter === "missing_stems") return s.has_cdlc && !s.has_stems;
      if (currentFilter === "missing_drums") return !s.has_drums;
      if (currentFilter === "ready") return s.has_stems && s.has_drums;
      return true;
    });

    // Sort by artist, then title
    filtered.sort(function (a, b) {
      var sa = songs[a]; var sb = songs[b];
      var cmp = (sa.artist || "").localeCompare(sb.artist || "");
      if (cmp !== 0) return cmp;
      return (sa.title || "").localeCompare(sb.title || "");
    });

    if (filtered.length === 0) {
      container.innerHTML = '<div style="padding: 40px; text-align: center; color: #5a5a7e;">No songs match the current filter.</div>';
      document.getElementById("library-count").textContent = "0 songs";
      return;
    }

    var html = "";
    for (var i = 0; i < filtered.length; i++) {
      var key = filtered[i];
      var s = songs[key];
      var isSelected = selectedSongs[key];

      html += '<div class="dash-song-row' + (isSelected ? " selected" : "") + '" data-key="' + escHtml(key) + '" ' +
        'style="display: grid; grid-template-columns: 40px 2fr 1.5fr 180px 160px; padding: 10px 16px; border-bottom: 1px solid #2a2a4e; align-items: center; font-size: 13px;">';

      // Checkbox
      html += '<div><input type="checkbox" ' + (isSelected ? "checked" : "") +
        ' onchange="DashboardUI.toggleSelect(\'' + escHtml(key) + '\')" style="cursor: pointer;" /></div>';

      // Title
      html += '<div style="color: #fff; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="' + escHtml(s.title || key) + '">' + escHtml(s.title || key) + '</div>';

      // Artist
      html += '<div style="color: #8888aa; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">' + escHtml(s.artist || "--") + '</div>';

      // Status badges
      html += '<div style="display: flex; gap: 4px; flex-wrap: wrap;">';
      html += badge("🎸", "CDLC", s.has_cdlc);
      html += badge("🥁", "Drums", s.has_drums);
      html += badge("🎤", "Vocals", s.has_vocals);
      html += badge("🎵", "Stems", s.has_stems);
      html += '</div>';

      // Actions
      html += '<div style="display: flex; gap: 4px;">';
      if (s.has_cdlc && !s.has_stems) {
        html += '<button class="dash-action-btn" style="background: #6c63ff; color: #fff;" onclick="DashboardUI.processSingle(\'' + escHtml(key) + '\')">Process</button>';
      }
      if (s.has_cdlc && s.has_stems && s.clonehero_path && !s.combined) {
        html += '<button class="dash-action-btn" style="background: #22c55e; color: #fff;" onclick="DashboardUI.combineSingle(\'' + escHtml(key) + '\')">Combine</button>';
      }
      if (s.combined || (s.has_stems && s.has_drums)) {
        html += '<button class="dash-action-btn" style="background: rgba(108,99,255,0.2); color: #a5a0ff; border: 1px solid #6c63ff;" onclick="DashboardUI.playSong(\'' + escHtml(key) + '\')">Play</button>';
      }
      html += '</div>';

      html += '</div>';
    }

    container.innerHTML = html;
    document.getElementById("library-count").textContent = filtered.length + " of " + keys.length + " songs";
    updateBulkActions();
  }

  function badge(icon, label, present) {
    var cls = present ? "present" : "missing";
    var mark = present ? "✓" : "✗";
    return '<span class="dash-badge ' + cls + '" title="' + label + '">' + icon + ' ' + mark + '</span>';
  }

  function filterLibrary() {
    renderLibrary();
  }

  function setFilter(filter) {
    currentFilter = filter;
    var btns = document.querySelectorAll(".dash-filter");
    for (var i = 0; i < btns.length; i++) {
      var isActive = btns[i].getAttribute("data-filter") === filter;
      btns[i].style.borderColor = isActive ? "#6c63ff" : "#3a3a5e";
      btns[i].style.background = isActive ? "rgba(108,99,255,0.2)" : "transparent";
      btns[i].style.color = isActive ? "#a5a0ff" : "#8888aa";
      btns[i].style.fontWeight = isActive ? "600" : "400";
    }
    renderLibrary();
  }

  // ── Selection ──

  function toggleSelect(key) {
    if (selectedSongs[key]) {
      delete selectedSongs[key];
    } else {
      selectedSongs[key] = true;
    }
    updateBulkActions();
    // Update row style
    var rows = document.querySelectorAll('.dash-song-row[data-key="' + key + '"]');
    for (var i = 0; i < rows.length; i++) {
      if (selectedSongs[key]) {
        rows[i].classList.add("selected");
      } else {
        rows[i].classList.remove("selected");
      }
    }
  }

  function toggleSelectAll() {
    var checkbox = document.getElementById("select-all");
    var songs = library.songs || {};
    var keys = Object.keys(songs);

    if (checkbox.checked) {
      for (var i = 0; i < keys.length; i++) {
        selectedSongs[keys[i]] = true;
      }
    } else {
      selectedSongs = {};
    }
    renderLibrary();
  }

  function clearSelection() {
    selectedSongs = {};
    var checkbox = document.getElementById("select-all");
    if (checkbox) checkbox.checked = false;
    renderLibrary();
  }

  function updateBulkActions() {
    var count = Object.keys(selectedSongs).length;
    var bar = document.getElementById("bulk-actions");
    if (count > 0) {
      bar.style.display = "flex";
      document.getElementById("bulk-count").textContent = count + " selected";
    } else {
      bar.style.display = "none";
    }
  }

  // ── Stats ──

  function updateStats() {
    api("GET", "/stats").then(function (data) {
      document.getElementById("stat-total").textContent = data.total + " songs";
      document.getElementById("stat-stems").textContent = data.with_stems + " with stems";
      document.getElementById("stat-drums").textContent = data.with_drums + " with drums";
      document.getElementById("stat-combined").textContent = data.combined + " combined";
    }).catch(function () {});
  }

  // ── Settings ──

  function loadSettings() {
    api("GET", "/settings").then(function (data) {
      var cdlcInput = document.getElementById("proc-cdlc-dir");
      var outputInput = document.getElementById("proc-output-dir");
      var apiInput = document.getElementById("proc-api-key");
      var setCdlc = document.getElementById("set-cdlc-dir");
      var setCh = document.getElementById("set-ch-dir");
      var setStems = document.getElementById("set-stems-dir");

      if (cdlcInput) cdlcInput.value = data.cdlc_dir || "";
      if (outputInput) outputInput.value = data.stems_dir || data.output_dir || "";
      if (setCdlc) setCdlc.value = data.cdlc_dir || "";
      if (setCh) setCh.value = data.clonehero_dir || "";
      if (setStems) setStems.value = data.stems_dir || "";

      var apiStatus = document.getElementById("api-key-status");
      if (apiStatus) {
        if (data.has_api_key) {
          apiStatus.textContent = "Key saved: " + data.replicate_api_key_masked;
          apiStatus.style.color = "#22c55e";
        } else {
          apiStatus.textContent = "No API key configured";
          apiStatus.style.color = "#ef4444";
        }
      }
    }).catch(function () {});
  }

  function saveProcessSettings() {
    var cdlcDir = document.getElementById("proc-cdlc-dir").value;
    var outputDir = document.getElementById("proc-output-dir").value;
    var apiKey = document.getElementById("proc-api-key").value;

    var settings = {
      cdlc_dir: cdlcDir,
      stems_dir: outputDir,
      output_dir: outputDir,
    };
    if (apiKey) settings.replicate_api_key = apiKey;

    api("POST", "/settings", settings).then(function () {
      showToast("Settings saved");
      loadSettings();
    }).catch(function (err) {
      showToast("Failed to save: " + err.message, true);
    });
  }

  function saveAllSettings() {
    var settings = {
      cdlc_dir: document.getElementById("set-cdlc-dir").value,
      clonehero_dir: document.getElementById("set-ch-dir").value,
      stems_dir: document.getElementById("set-stems-dir").value,
    };
    var apiKey = document.getElementById("set-api-key").value;
    if (apiKey) settings.replicate_api_key = apiKey;

    settings.output_dir = settings.stems_dir;

    api("POST", "/settings", settings).then(function () {
      showToast("Settings saved");
      toggleSettingsModal();
      loadSettings();
    }).catch(function (err) {
      showToast("Failed to save: " + err.message, true);
    });
  }

  function toggleSettingsModal() {
    var modal = document.getElementById("settings-modal");
    if (modal.style.display === "flex") {
      modal.style.display = "none";
    } else {
      modal.style.display = "flex";
      loadSettings();
    }
  }

  function testApiKey() {
    var result = document.getElementById("api-test-result");
    result.textContent = "Testing...";
    result.style.color = "#aaaabb";

    // Save the key first if provided
    var apiKey = document.getElementById("set-api-key").value;
    var saveFirst = apiKey
      ? api("POST", "/settings", { replicate_api_key: apiKey })
      : Promise.resolve();

    saveFirst.then(function () {
      return api("POST", "/test-api");
    }).then(function (data) {
      if (data.ok) {
        result.textContent = "Connected successfully";
        result.style.color = "#22c55e";
      } else {
        result.textContent = "Failed: " + data.error;
        result.style.color = "#ef4444";
      }
    }).catch(function (err) {
      result.textContent = "Error: " + err.message;
      result.style.color = "#ef4444";
    });
  }

  // ── Scanning ──

  function scanLibrary() {
    var cdlcDir = document.getElementById("proc-cdlc-dir").value;
    var outputDir = document.getElementById("proc-output-dir").value;
    var status = document.getElementById("scan-status");

    status.textContent = "Scanning directories...";
    status.style.color = "#6c63ff";

    api("POST", "/scan", {
      cdlc_dir: cdlcDir,
      stems_dir: outputDir,
    }).then(function (data) {
      status.innerHTML = "Found <strong>" + data.total_songs + "</strong> songs " +
        "(" + data.scan_counts.cdlc + " CDLC, " +
        data.scan_counts.clonehero + " Clone Hero, " +
        data.scan_counts.stems + " with stems)";
      status.style.color = "#22c55e";
      loadLibrary();
      showCostEstimate();
    }).catch(function (err) {
      status.textContent = "Scan failed: " + err.message;
      status.style.color = "#ef4444";
    });
  }

  function rescanLibrary() {
    toggleSettingsModal();
    switchTab("process");
    scanLibrary();
  }

  function showCostEstimate() {
    api("GET", "/stats").then(function (data) {
      if (data.missing_stems > 0) {
        var costBox = document.getElementById("cost-estimate");
        var costDetails = document.getElementById("cost-details");
        costBox.style.display = "block";
        costDetails.innerHTML =
          "<strong>" + data.missing_stems + "</strong> songs missing stems<br>" +
          "Average cost per song: <strong>$0.021</strong><br>" +
          "Estimated total: <strong>$" + data.est_cost.toFixed(2) + "</strong><br>" +
          "Estimated time: <strong>~" + Math.ceil(data.missing_stems * 94 / 60) + " min</strong>";

        document.getElementById("btn-process-all").style.display = "inline-block";
      }
    });
  }

  // ── Processing ──

  function processAll() {
    var apiKey = document.getElementById("proc-api-key").value;
    var outputDir = document.getElementById("proc-output-dir").value;

    api("POST", "/process", {
      song_keys: "all",
      api_key: apiKey,
      output_dir: outputDir,
    }).then(function (data) {
      showToast("Processing " + data.queued + " songs...");
      document.getElementById("btn-process-cancel").style.display = "inline-block";
      startPolling();
    }).catch(function (err) {
      showToast("Failed: " + err.message, true);
    });
  }

  function processSelected() {
    var keys = Object.keys(selectedSongs);
    if (keys.length === 0) {
      showToast("No songs selected", true);
      return;
    }

    var apiKey = document.getElementById("proc-api-key").value;
    var outputDir = document.getElementById("proc-output-dir").value;

    api("POST", "/process", {
      song_keys: keys,
      api_key: apiKey,
      output_dir: outputDir,
    }).then(function (data) {
      showToast("Processing " + data.queued + " songs...");
      clearSelection();
      switchTab("process");
      document.getElementById("btn-process-cancel").style.display = "inline-block";
      startPolling();
    }).catch(function (err) {
      showToast("Failed: " + err.message, true);
    });
  }

  function processSingle(key) {
    var apiKey = document.getElementById("proc-api-key") ? document.getElementById("proc-api-key").value : "";
    var outputDir = document.getElementById("proc-output-dir") ? document.getElementById("proc-output-dir").value : "";

    api("POST", "/process", {
      song_keys: [key],
      api_key: apiKey,
      output_dir: outputDir,
    }).then(function (data) {
      showToast("Processing started for 1 song");
      switchTab("process");
      startPolling();
    }).catch(function (err) {
      showToast("Failed: " + err.message, true);
    });
  }

  function cancelProcessing() {
    api("POST", "/process/cancel").then(function (data) {
      showToast("Cancelled. " + data.drained + " songs removed from queue.");
      document.getElementById("btn-process-cancel").style.display = "none";
    }).catch(function (err) {
      showToast("Cancel failed: " + err.message, true);
    });
  }

  function checkProcessStatus() {
    api("GET", "/process/status").then(function (data) {
      renderProcessStatus(data);
      if (data.running) {
        startPolling();
      } else {
        stopPolling();
      }
    }).catch(function () {});
  }

  function renderProcessStatus(data) {
    var section = document.getElementById("progress-section");
    var noProgress = document.getElementById("no-progress");

    var hasActivity = data.running || data.completed.length > 0 || data.failed.length > 0;

    if (!hasActivity) {
      section.style.display = "none";
      noProgress.style.display = "block";
      return;
    }

    section.style.display = "block";
    noProgress.style.display = "none";

    // Progress bar
    var total = data.total || 1;
    var done = data.done || 0;
    var pct = Math.round((done / total) * 100);

    document.getElementById("progress-bar").style.width = pct + "%";
    document.getElementById("progress-pct").textContent = pct + "%";
    document.getElementById("progress-count").textContent = done + " / " + total;

    if (data.running) {
      document.getElementById("progress-label").textContent = "Processing...";
      document.getElementById("btn-process-cancel").style.display = "inline-block";
    } else {
      document.getElementById("progress-label").textContent = "Complete";
      document.getElementById("btn-process-cancel").style.display = "none";
    }

    // ETA
    var etaEl = document.getElementById("progress-eta");
    if (data.eta_seconds && data.running) {
      var mins = Math.ceil(data.eta_seconds / 60);
      etaEl.textContent = "ETA: ~" + mins + " min";
    } else if (!data.running) {
      etaEl.textContent = "";
    } else {
      etaEl.textContent = "ETA: calculating...";
    }

    // Current song
    var currentBox = document.getElementById("current-song-box");
    if (data.current) {
      currentBox.style.display = "block";
      var songData = (library.songs || {})[data.current];
      var displayName = songData
        ? (songData.artist ? songData.artist + " - " : "") + songData.title
        : data.current;
      document.getElementById("current-song-name").textContent = displayName;
    } else {
      currentBox.style.display = "none";
    }

    // Completed list
    var completedEl = document.getElementById("completed-list");
    if (data.completed.length > 0) {
      completedEl.innerHTML = data.completed.map(function (key) {
        var s = (library.songs || {})[key];
        var name = s ? (s.artist ? s.artist + " - " : "") + s.title : key;
        return '<div style="padding: 3px 0;">✅ ' + escHtml(name) + '</div>';
      }).join("");
    } else {
      completedEl.innerHTML = '<span style="color: #5a5a7e;">None yet</span>';
    }

    // Failed list
    var failedEl = document.getElementById("failed-list");
    if (data.failed.length > 0) {
      failedEl.innerHTML = data.failed.map(function (f) {
        var s = (library.songs || {})[f.key];
        var name = s ? (s.artist ? s.artist + " - " : "") + s.title : f.key;
        return '<div style="padding: 3px 0;">❌ ' + escHtml(name) +
          ' <span style="color: #5a5a7e;">— ' + escHtml(f.error || "Unknown error") + '</span></div>';
      }).join("");
    } else {
      failedEl.innerHTML = '<span style="color: #5a5a7e;">None</span>';
    }
  }

  function startPolling() {
    if (pollInterval) return;
    pollInterval = setInterval(function () {
      api("GET", "/process/status").then(function (data) {
        renderProcessStatus(data);
        if (!data.running) {
          stopPolling();
          loadLibrary();
          showToast("Processing complete: " + data.completed.length + " done, " + data.failed.length + " failed");
        }
      }).catch(function () {});
    }, 2000);
  }

  function stopPolling() {
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
  }

  // ── Combine Tab ──

  function loadCombineData() {
    api("GET", "/library").then(function (data) {
      library = data;
      renderCombinePanels();
      renderPairedSongs();
    });
  }

  function renderCombinePanels() {
    var songs = library.songs || {};
    var keys = Object.keys(songs);

    // CDLC songs (that need drums pairing)
    var cdlcSongs = keys.filter(function (k) {
      return songs[k].has_cdlc && !songs[k].clonehero_path;
    });
    var cdlcList = document.getElementById("cdlc-list");
    document.getElementById("cdlc-count").textContent = cdlcSongs.length + " songs";

    if (cdlcSongs.length === 0) {
      cdlcList.innerHTML = '<div style="padding: 24px; text-align: center; color: #5a5a7e; font-size: 13px;">No unmatched CDLC songs</div>';
    } else {
      cdlcList.innerHTML = cdlcSongs.map(function (k) {
        var s = songs[k];
        var isSelected = combineSelection.cdlc === k;
        return '<div class="combine-item' + (isSelected ? " selected-pair" : "") + '" onclick="DashboardUI.selectCombineCdlc(\'' + escHtml(k) + '\')">' +
          '<div style="font-size: 13px; color: #fff; font-weight: 500;">' + escHtml(s.title || k) + '</div>' +
          '<div style="font-size: 11px; color: #6a6a8e;">' + escHtml(s.artist || "") + '</div>' +
          '</div>';
      }).join("");
    }

    // Clone Hero songs (standalone)
    var chSongs = keys.filter(function (k) {
      return songs[k].clonehero_path && !songs[k].has_cdlc;
    });
    var chList = document.getElementById("ch-list");
    document.getElementById("ch-count").textContent = chSongs.length + " songs";

    if (chSongs.length === 0) {
      chList.innerHTML = '<div style="padding: 24px; text-align: center; color: #5a5a7e; font-size: 13px;">No unmatched Clone Hero songs</div>';
    } else {
      chList.innerHTML = chSongs.map(function (k) {
        var s = songs[k];
        var isSelected = combineSelection.ch === k;
        return '<div class="combine-item' + (isSelected ? " selected-pair" : "") + '" onclick="DashboardUI.selectCombineCh(\'' + escHtml(k) + '\')">' +
          '<div style="font-size: 13px; color: #fff; font-weight: 500;">' + escHtml(s.title || k) + '</div>' +
          '<div style="font-size: 11px; color: #6a6a8e;">' + escHtml(s.artist || "") + '</div>' +
          '</div>';
      }).join("");
    }
  }

  function selectCombineCdlc(key) {
    combineSelection.cdlc = (combineSelection.cdlc === key) ? null : key;
    renderCombinePanels();
    tryAutoPair();
  }

  function selectCombineCh(key) {
    combineSelection.ch = (combineSelection.ch === key) ? null : key;
    renderCombinePanels();
    tryAutoPair();
  }

  function tryAutoPair() {
    if (combineSelection.cdlc && combineSelection.ch) {
      // Both selected, prompt to pair
      var songs = library.songs || {};
      var cdlc = songs[combineSelection.cdlc];
      var ch = songs[combineSelection.ch];

      var name1 = (cdlc.artist ? cdlc.artist + " - " : "") + cdlc.title;
      var name2 = (ch.artist ? ch.artist + " - " : "") + ch.title;

      if (confirm("Pair these songs?\n\nCDLC: " + name1 + "\nClone Hero: " + name2)) {
        api("POST", "/pair", {
          cdlc_key: combineSelection.cdlc,
          clonehero_key: combineSelection.ch,
        }).then(function () {
          showToast("Songs paired");
          combineSelection = { cdlc: null, ch: null };
          loadCombineData();
        }).catch(function (err) {
          showToast("Pair failed: " + err.message, true);
        });
      }
    }
  }

  function renderPairedSongs() {
    var songs = library.songs || {};
    var paired = Object.keys(songs).filter(function (k) {
      return songs[k].has_cdlc && songs[k].clonehero_path;
    });

    var container = document.getElementById("paired-list");

    if (paired.length === 0) {
      container.innerHTML = '<div style="padding: 24px; text-align: center; color: #5a5a7e; font-size: 13px;">No pairings yet. Use Auto-Match or click songs to pair them.</div>';
      return;
    }

    container.innerHTML = paired.map(function (k) {
      var s = songs[k];
      var name = (s.artist ? s.artist + " - " : "") + s.title;
      return '<div style="display: flex; align-items: center; justify-content: space-between; padding: 10px 16px; border-bottom: 1px solid #2a2a4e;">' +
        '<div>' +
          '<div style="font-size: 13px; color: #fff; font-weight: 500;">' + escHtml(name) + '</div>' +
          '<div style="font-size: 11px; color: #6a6a8e;">CDLC + Clone Hero' + (s.has_stems ? " + Stems" : "") + '</div>' +
        '</div>' +
        '<div style="display: flex; gap: 6px;">' +
          (s.combined
            ? '<span style="font-size: 12px; color: #22c55e; font-weight: 600;">Combined ✓</span>'
            : '<button class="dash-action-btn" style="background: #22c55e; color: #fff;" onclick="DashboardUI.combineSingle(\'' + escHtml(k) + '\')">Combine</button>'
          ) +
        '</div>' +
      '</div>';
    }).join("");
  }

  function autoMatch() {
    var status = document.getElementById("match-status");
    status.textContent = "Finding matches...";
    status.style.color = "#6c63ff";

    api("POST", "/auto-match").then(function (data) {
      if (data.count === 0) {
        status.textContent = "No matches found. Try scanning more directories.";
        status.style.color = "#ef4444";
        return;
      }

      pendingMatches = data.matches;
      status.textContent = "Found " + data.count + " potential matches. Applying...";
      status.style.color = "#22c55e";

      // Show matches and let user confirm
      var msg = "Found " + data.count + " matches:\n\n";
      for (var i = 0; i < Math.min(data.matches.length, 10); i++) {
        var m = data.matches[i];
        msg += "• " + m.cdlc_title + "  ↔  " + m.ch_title + " (" + Math.round(m.similarity * 100) + "%)\n";
      }
      if (data.matches.length > 10) {
        msg += "\n...and " + (data.matches.length - 10) + " more.";
      }
      msg += "\n\nApply all matches?";

      if (confirm(msg)) {
        applyMatches(data.matches);
      } else {
        status.textContent = data.count + " matches found but not applied.";
        status.style.color = "#aaaabb";
      }
    }).catch(function (err) {
      status.textContent = "Match failed: " + err.message;
      status.style.color = "#ef4444";
    });
  }

  function applyMatches(matches) {
    var applied = 0;
    var chain = Promise.resolve();

    for (var i = 0; i < matches.length; i++) {
      (function (m) {
        chain = chain.then(function () {
          return api("POST", "/pair", {
            cdlc_key: m.cdlc_key,
            clonehero_key: m.clonehero_key,
          }).then(function () { applied++; });
        }).catch(function () {}); // skip individual failures
      })(matches[i]);
    }

    chain.then(function () {
      var status = document.getElementById("match-status");
      status.textContent = "Applied " + applied + " of " + matches.length + " matches.";
      status.style.color = "#22c55e";
      loadCombineData();
    });
  }

  function combineSingle(key) {
    showToast("Combining...");
    api("POST", "/combine", { song_key: key }).then(function (data) {
      showToast("Combined successfully → " + data.output_path);
      loadLibrary();
      if (document.getElementById("tab-combine").style.display !== "none") {
        loadCombineData();
      }
    }).catch(function (err) {
      showToast("Combine failed: " + err.message, true);
    });
  }

  // ── Multiplayer Tab ──

  function refreshRooms() {
    fetch("/api/plugins/multiplayer/rooms")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var container = document.getElementById("rooms-list");
        if (!data.rooms || data.rooms.length === 0) {
          container.innerHTML = '<div style="padding: 40px; text-align: center; color: #5a5a7e; font-size: 14px; grid-column: 1 / -1;">No active rooms. Create one to start a session.</div>';
          return;
        }

        container.innerHTML = data.rooms.map(function (r) {
          var statusColor = r.playing ? "#22c55e" : "#6c63ff";
          var statusText = r.playing ? "Playing" : "Waiting";
          var players = r.players || [];

          return '<div class="room-card">' +
            '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">' +
              '<span style="font-size: 16px; font-weight: 600; color: #fff; font-family: monospace;">' + escHtml(r.room_id) + '</span>' +
              '<span style="padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; background: ' + statusColor + '22; color: ' + statusColor + '; border: 1px solid ' + statusColor + '44;">' + statusText + '</span>' +
            '</div>' +
            '<div style="font-size: 13px; color: #8888aa; margin-bottom: 8px;">Song: ' + escHtml(r.song_key || "None selected") + '</div>' +
            '<div style="font-size: 13px; color: #8888aa; margin-bottom: 12px;">Players: ' + r.player_count + '/4</div>' +
            '<div style="display: flex; flex-wrap: wrap; gap: 6px;">' +
              players.map(function (p) {
                var instIcon = { guitar: "🎸", bass: "🎸", drums: "🥁", vocals: "🎤" }[p.instrument] || "❓";
                return '<span style="padding: 4px 10px; background: #1a1a2e; border-radius: 6px; font-size: 12px; color: #aaaabb;">' +
                  instIcon + " " + escHtml(p.name) +
                  (p.ready ? ' ✅' : '') +
                '</span>';
              }).join("") +
            '</div>' +
            '<div style="margin-top: 12px;">' +
              '<button class="dash-action-btn" style="background: #ef4444; color: #fff;" onclick="DashboardUI.deleteRoom(\'' + escHtml(r.room_id) + '\')">Delete</button>' +
            '</div>' +
          '</div>';
        }).join("");
      })
      .catch(function () {
        document.getElementById("rooms-list").innerHTML = '<div style="padding: 40px; text-align: center; color: #ef4444;">Failed to load rooms</div>';
      });
  }

  function showCreateRoom() {
    // Populate song select with songs that have stems
    var select = document.getElementById("room-song-select");
    var songs = library.songs || {};
    var options = '<option value="">Select a song...</option>';
    Object.keys(songs).forEach(function (k) {
      var s = songs[k];
      if (s.has_stems) {
        var name = (s.artist ? s.artist + " - " : "") + s.title;
        options += '<option value="' + escHtml(k) + '">' + escHtml(name) + '</option>';
      }
    });
    select.innerHTML = options;
    document.getElementById("create-room-modal").style.display = "flex";
  }

  function hideCreateRoom() {
    document.getElementById("create-room-modal").style.display = "none";
  }

  function createRoom() {
    var hostName = document.getElementById("room-host-name").value || "Host";
    var songKey = document.getElementById("room-song-select").value;

    fetch("/api/plugins/multiplayer/rooms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ song_key: songKey, host_name: hostName }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        hideCreateRoom();
        showToast("Room created: " + data.room_id);
        refreshRooms();
      })
      .catch(function (err) {
        showToast("Failed to create room: " + err.message, true);
      });
  }

  function deleteRoom(roomId) {
    if (!confirm("Delete room " + roomId + "?")) return;

    fetch("/api/plugins/multiplayer/rooms/" + roomId, { method: "DELETE" })
      .then(function () {
        showToast("Room deleted");
        refreshRooms();
      })
      .catch(function (err) {
        showToast("Failed: " + err.message, true);
      });
  }

  // ── Play Song ──

  function playSong(key) {
    // Emit SlopSmith event if available
    if (window.slopsmith && window.slopsmith.emit) {
      window.slopsmith.emit("song:play", { songKey: key });
      showToast("Playing: " + key);
    } else {
      showToast("SlopSmith playback not available from dashboard. Open the main player.");
    }
  }

  // ── Utilities ──

  function escHtml(str) {
    var div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }

  function showToast(msg, isError) {
    var existing = document.getElementById("dash-toast");
    if (existing) existing.remove();

    var toast = document.createElement("div");
    toast.id = "dash-toast";
    toast.textContent = msg;
    toast.style.cssText = "position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); " +
      "padding: 10px 24px; border-radius: 8px; font-size: 13px; font-weight: 500; z-index: 9999; " +
      "transition: opacity 0.3s; " +
      (isError
        ? "background: #ef4444; color: #fff; border: 1px solid #dc2626;"
        : "background: #22c55e; color: #fff; border: 1px solid #16a34a;");

    document.body.appendChild(toast);
    setTimeout(function () {
      toast.style.opacity = "0";
      setTimeout(function () { toast.remove(); }, 300);
    }, 3000);
  }

  // ── Initialize ──

  // Load library and settings on first paint
  setTimeout(function () {
    loadLibrary();
    loadSettings();
    checkProcessStatus();
  }, 200);

  // ── Expose public API for onclick handlers ──

  window.DashboardUI = {
    switchTab: switchTab,
    filterLibrary: filterLibrary,
    setFilter: setFilter,
    toggleSelect: toggleSelect,
    toggleSelectAll: toggleSelectAll,
    clearSelection: clearSelection,
    processSelected: processSelected,
    processAll: processAll,
    processSingle: processSingle,
    cancelProcessing: cancelProcessing,
    combineSingle: combineSingle,
    scanLibrary: scanLibrary,
    saveProcessSettings: saveProcessSettings,
    saveAllSettings: saveAllSettings,
    toggleSettingsModal: toggleSettingsModal,
    testApiKey: testApiKey,
    loadSettings: loadSettings,
    rescanLibrary: rescanLibrary,
    selectCombineCdlc: selectCombineCdlc,
    selectCombineCh: selectCombineCh,
    autoMatch: autoMatch,
    refreshRooms: refreshRooms,
    showCreateRoom: showCreateRoom,
    hideCreateRoom: hideCreateRoom,
    createRoom: createRoom,
    deleteRoom: deleteRoom,
    playSong: playSong,
  };

  console.log("[Dashboard] Stem Manager Dashboard loaded");
})();
