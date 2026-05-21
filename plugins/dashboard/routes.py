"""Dashboard plugin -- backend routes for the Stem Manager Dashboard.

Provides library scanning, Demucs stem processing, chart combining,
and integration with the multiplayer plugin. All heavy work runs in
background threads so the API never blocks.
"""

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import JSONResponse

# ── Import sibling scripts from repo root ──
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ── In-memory processing state ──

processing_state = {
    "queue": [],       # song keys waiting to be processed
    "current": None,   # song key currently processing
    "completed": [],   # finished song keys
    "failed": [],      # [{"key": str, "error": str}, ...]
    "running": False,
    "started_at": None,
    "times": [],       # per-song durations for ETA calc
}


def setup(app, context):
    config_dir = Path(context["config_dir"])
    log = context["log"]
    library_path = config_dir / "library.json"

    # ── Library helpers ──

    def load_library():
        if library_path.exists():
            try:
                return json.loads(library_path.read_text())
            except (json.JSONDecodeError, IOError):
                pass
        return {"songs": {}, "settings": {
            "cdlc_dir": "", "clonehero_dir": "", "stems_dir": "",
            "replicate_api_key": "", "output_dir": "",
        }}

    def save_library(lib):
        library_path.write_text(json.dumps(lib, indent=2))

    def make_song_key(title, artist=""):
        """Create a stable key from title + artist."""
        raw = f"{artist} - {title}" if artist else title
        return raw.strip().lower().replace(" ", "_").replace("/", "_")[:120]

    # ── Endpoints ──

    @app.get("/api/plugins/dashboard/library")
    async def get_library():
        """Return full library with per-song status."""
        lib = load_library()
        return lib

    @app.post("/api/plugins/dashboard/scan")
    async def scan_library(data: dict = None):
        """Scan directories for CDLC, Clone Hero, and stems. Build library index.

        Body (optional): {
            "cdlc_dir": "/path/...",
            "clonehero_dir": "/path/...",
            "stems_dir": "/path/..."
        }
        """
        lib = load_library()
        settings = lib.get("settings", {})

        # Override settings with request body if provided
        if data:
            for key in ("cdlc_dir", "clonehero_dir", "stems_dir"):
                if data.get(key):
                    settings[key] = data[key]

        lib["settings"] = settings
        songs = lib.get("songs", {})
        scan_counts = {"cdlc": 0, "clonehero": 0, "stems": 0}

        # 1. Scan CDLC directory for .psarc files
        cdlc_dir = settings.get("cdlc_dir", "")
        if cdlc_dir and Path(cdlc_dir).exists():
            for psarc in sorted(Path(cdlc_dir).rglob("*.psarc")):
                name = psarc.stem.replace("_p", "").replace("_m", "")
                key = make_song_key(name)
                if key not in songs:
                    songs[key] = {
                        "title": name, "artist": "",
                        "cdlc_path": None, "clonehero_path": None,
                        "stems_path": None,
                        "has_stems": False, "has_drums": False,
                        "has_vocals": False, "has_cdlc": False,
                        "stem_status": "none", "combined": False,
                    }
                songs[key]["cdlc_path"] = str(psarc)
                songs[key]["has_cdlc"] = True
                # Try to parse artist from filename: "Artist - Title_p.psarc"
                if " - " in name:
                    parts = name.split(" - ", 1)
                    songs[key]["artist"] = parts[0].strip()
                    songs[key]["title"] = parts[1].strip()
                scan_counts["cdlc"] += 1

            # Also scan for loose audio files (mp3, ogg, wav, flac)
            for ext in ("*.mp3", "*.ogg", "*.wav", "*.flac"):
                for audio_file in sorted(Path(cdlc_dir).rglob(ext)):
                    # Skip stems sub-files and very small files
                    if audio_file.stat().st_size < 50_000:
                        continue
                    stem_part = audio_file.stem.lower()
                    if stem_part in ("drums", "bass", "vocals", "guitar", "other",
                                     "original", "backing"):
                        continue
                    name = audio_file.stem
                    key = make_song_key(name)
                    if key not in songs:
                        songs[key] = {
                            "title": name, "artist": "",
                            "cdlc_path": None, "clonehero_path": None,
                            "stems_path": None,
                            "has_stems": False, "has_drums": False,
                            "has_vocals": False, "has_cdlc": False,
                            "stem_status": "none", "combined": False,
                        }
                    songs[key]["cdlc_path"] = str(audio_file)
                    songs[key]["has_cdlc"] = True
                    if " - " in name:
                        parts = name.split(" - ", 1)
                        songs[key]["artist"] = parts[0].strip()
                        songs[key]["title"] = parts[1].strip()
                    scan_counts["cdlc"] += 1

        # 2. Scan Clone Hero directory for song folders
        ch_dir = settings.get("clonehero_dir", "")
        if ch_dir and Path(ch_dir).exists():
            for song_dir in sorted(Path(ch_dir).iterdir()):
                if not song_dir.is_dir():
                    continue
                # CH songs must have a chart file
                has_chart = any(
                    (song_dir / name).exists()
                    for name in ("notes.chart", "Notes.chart",
                                 "notes.mid", "Notes.mid")
                )
                if not has_chart:
                    # Check one level deeper
                    for sub in song_dir.iterdir():
                        if sub.is_dir():
                            has_chart = any(
                                (sub / name).exists()
                                for name in ("notes.chart", "Notes.chart",
                                             "notes.mid", "Notes.mid")
                            )
                            if has_chart:
                                song_dir = sub
                                break
                if not has_chart:
                    continue

                # Parse song.ini for metadata
                ini_path = song_dir / "song.ini"
                title = song_dir.name
                artist = ""
                if ini_path.exists():
                    try:
                        import configparser
                        cfg = configparser.ConfigParser()
                        cfg.read(str(ini_path), encoding="utf-8")
                        section = "song" if "song" in cfg else ("Song" if "Song" in cfg else None)
                        if section:
                            title = cfg.get(section, "name", fallback=title)
                            artist = cfg.get(section, "artist", fallback="")
                    except Exception:
                        pass

                key = make_song_key(title, artist)
                if key not in songs:
                    songs[key] = {
                        "title": title, "artist": artist,
                        "cdlc_path": None, "clonehero_path": None,
                        "stems_path": None,
                        "has_stems": False, "has_drums": False,
                        "has_vocals": False, "has_cdlc": False,
                        "stem_status": "none", "combined": False,
                    }
                songs[key]["clonehero_path"] = str(song_dir)
                songs[key]["has_drums"] = True
                if not songs[key]["artist"]:
                    songs[key]["artist"] = artist
                if songs[key]["title"] == key or not songs[key]["title"]:
                    songs[key]["title"] = title
                scan_counts["clonehero"] += 1

        # 3. Scan stems directory
        stems_dir = settings.get("stems_dir", "")
        if stems_dir and Path(stems_dir).exists():
            for song_dir in sorted(Path(stems_dir).iterdir()):
                if not song_dir.is_dir():
                    continue
                # Check for stem files
                stem_names = ["drums", "bass", "vocals", "guitar", "other"]
                found = {}
                for sname in stem_names:
                    for ext in (".mp3", ".ogg", ".wav", ".flac"):
                        if (song_dir / f"{sname}{ext}").exists():
                            found[sname] = True
                            break
                if not found:
                    continue

                name = song_dir.name
                key = make_song_key(name)
                if key not in songs:
                    songs[key] = {
                        "title": name, "artist": "",
                        "cdlc_path": None, "clonehero_path": None,
                        "stems_path": None,
                        "has_stems": False, "has_drums": False,
                        "has_vocals": False, "has_cdlc": False,
                        "stem_status": "none", "combined": False,
                    }
                songs[key]["stems_path"] = str(song_dir)
                songs[key]["has_stems"] = len(found) >= 4
                songs[key]["has_drums"] = "drums" in found
                songs[key]["has_vocals"] = "vocals" in found
                songs[key]["stem_status"] = "complete" if len(found) >= 4 else "partial"
                scan_counts["stems"] += 1

        lib["songs"] = songs
        save_library(lib)

        log.info(
            f"Library scan complete: {scan_counts['cdlc']} CDLC, "
            f"{scan_counts['clonehero']} Clone Hero, "
            f"{scan_counts['stems']} stems folders"
        )

        return {
            "total_songs": len(songs),
            "scan_counts": scan_counts,
        }

    @app.post("/api/plugins/dashboard/process")
    async def start_processing(data: dict):
        """Start Demucs processing for selected songs (background thread).

        Body: {"song_keys": ["key1", "key2", ...]}
        Or: {"song_keys": "all"} to process all songs missing stems.
        """
        global processing_state

        if processing_state["running"]:
            raise HTTPException(409, "Processing already running. Wait or cancel.")

        lib = load_library()
        songs = lib.get("songs", {})
        settings = lib.get("settings", {})

        # Set API key if provided
        api_key = data.get("api_key") or settings.get("replicate_api_key", "")
        if api_key:
            os.environ["REPLICATE_API_TOKEN"] = api_key

        if not os.environ.get("REPLICATE_API_TOKEN"):
            raise HTTPException(400, "Replicate API key not set. Provide it in settings.")

        output_dir = data.get("output_dir") or settings.get("stems_dir") or settings.get("output_dir", "")
        if not output_dir:
            raise HTTPException(400, "No output/stems directory configured.")

        # Determine which songs to process
        requested = data.get("song_keys", [])
        if requested == "all":
            queue = [k for k, v in songs.items()
                     if v.get("stem_status") != "complete" and v.get("cdlc_path")]
        else:
            queue = [k for k in requested if k in songs and songs[k].get("cdlc_path")]

        if not queue:
            return {"queued": 0, "message": "No songs to process."}

        # Reset state
        processing_state = {
            "queue": list(queue),
            "current": None,
            "completed": [],
            "failed": [],
            "running": True,
            "started_at": time.time(),
            "times": [],
        }

        # Run processing in background thread
        def _process_worker():
            global processing_state
            try:
                from stem_separate import process_song as stem_process_song
            except ImportError as e:
                processing_state["running"] = False
                processing_state["failed"].append({
                    "key": "IMPORT_ERROR",
                    "error": f"Cannot import stem_separate: {e}"
                })
                return

            lib_inner = load_library()
            songs_inner = lib_inner.get("songs", {})

            while processing_state["queue"]:
                song_key = processing_state["queue"].pop(0)
                processing_state["current"] = song_key
                song = songs_inner.get(song_key, {})
                input_path = song.get("cdlc_path")

                if not input_path or not Path(input_path).exists():
                    processing_state["failed"].append({
                        "key": song_key,
                        "error": "Source file not found"
                    })
                    continue

                t0 = time.time()
                try:
                    result = stem_process_song(Path(input_path), Path(output_dir))
                    elapsed = time.time() - t0
                    processing_state["times"].append(elapsed)

                    if result:
                        processing_state["completed"].append(song_key)
                        # Update library
                        song_output = Path(output_dir) / Path(input_path).stem.replace("_p", "").replace("_m", "")
                        songs_inner[song_key]["stems_path"] = str(song_output)
                        songs_inner[song_key]["has_stems"] = True
                        songs_inner[song_key]["has_drums"] = True
                        songs_inner[song_key]["has_vocals"] = True
                        songs_inner[song_key]["stem_status"] = "complete"
                    else:
                        processing_state["failed"].append({
                            "key": song_key,
                            "error": "No stems returned"
                        })
                except Exception as e:
                    elapsed = time.time() - t0
                    processing_state["times"].append(elapsed)
                    processing_state["failed"].append({
                        "key": song_key,
                        "error": str(e)[:200]
                    })
                    log.error(f"Processing failed for {song_key}: {e}")

            # Save updated library
            lib_inner["songs"] = songs_inner
            save_library(lib_inner)

            processing_state["current"] = None
            processing_state["running"] = False
            log.info(
                f"Processing complete: {len(processing_state['completed'])} done, "
                f"{len(processing_state['failed'])} failed"
            )

        thread = threading.Thread(target=_process_worker, daemon=True)
        thread.start()

        return {
            "queued": len(queue),
            "message": f"Processing {len(queue)} songs in background.",
        }

    @app.post("/api/plugins/dashboard/process/cancel")
    async def cancel_processing():
        """Cancel processing by draining the queue."""
        global processing_state
        remaining = len(processing_state["queue"])
        processing_state["queue"] = []
        # Current song will finish, but no new ones will start
        return {"cancelled": True, "drained": remaining}

    @app.get("/api/plugins/dashboard/process/status")
    async def get_processing_status():
        """Get current processing status."""
        total = (
            len(processing_state["completed"]) +
            len(processing_state["failed"]) +
            len(processing_state["queue"]) +
            (1 if processing_state["current"] else 0)
        )
        done = len(processing_state["completed"]) + len(processing_state["failed"])

        # ETA calculation
        eta_seconds = None
        if processing_state["times"] and processing_state["running"]:
            avg_time = sum(processing_state["times"]) / len(processing_state["times"])
            remaining = len(processing_state["queue"]) + (1 if processing_state["current"] else 0)
            eta_seconds = avg_time * remaining

        return {
            "running": processing_state["running"],
            "current": processing_state["current"],
            "queue": processing_state["queue"],
            "completed": processing_state["completed"],
            "failed": processing_state["failed"],
            "total": total,
            "done": done,
            "eta_seconds": eta_seconds,
            "avg_time": (
                sum(processing_state["times"]) / len(processing_state["times"])
                if processing_state["times"] else None
            ),
        }

    @app.post("/api/plugins/dashboard/combine")
    async def combine_song(data: dict):
        """Combine CDLC + Clone Hero/YARC + stems for a song.

        Body: {
            "song_key": "...",
            "cdlc_path": "/path/...",       (optional, from library)
            "clonehero_path": "/path/...",   (optional, from library)
            "vocals_path": "/path/...",      (optional, separate vocal chart folder)
            "stems_path": "/path/...",       (optional, from library)
            "output_dir": "/path/..."        (optional, from settings)
        }
        """
        lib = load_library()
        songs = lib.get("songs", {})
        settings = lib.get("settings", {})
        song_key = data.get("song_key", "")

        song = songs.get(song_key, {})

        cdlc_path = data.get("cdlc_path") or song.get("cdlc_path")
        ch_path = data.get("clonehero_path") or song.get("clonehero_path")
        vocals_path = data.get("vocals_path") or song.get("vocals_path")
        stems_path = data.get("stems_path") or song.get("stems_path")
        output_dir = data.get("output_dir") or settings.get("output_dir") or settings.get("stems_dir", "")

        if not output_dir:
            raise HTTPException(400, "No output directory configured.")

        if not any([cdlc_path, ch_path, vocals_path, stems_path]):
            raise HTTPException(400, "Need at least one source (CDLC, Clone Hero, vocals, or stems).")

        try:
            from combine_charts import combine
        except ImportError as e:
            raise HTTPException(500, f"Cannot import combine_charts: {e}")

        output_path = Path(output_dir) / "combined" / (song_key or "unknown")

        try:
            cdlc = Path(cdlc_path) if cdlc_path else None
            ch = Path(ch_path) if ch_path else None
            vocals = Path(vocals_path) if vocals_path else None
            stems = Path(stems_path) if stems_path else None

            manifest = combine(cdlc, ch, stems, output_path, vocals_path=vocals)

            # Update library
            if song_key in songs:
                songs[song_key]["combined"] = True
                lib["songs"] = songs
                save_library(lib)

            return {
                "success": True,
                "output_path": str(output_path),
                "manifest": manifest,
            }
        except Exception as e:
            log.error(f"Combine failed for {song_key}: {traceback.format_exc()}")
            raise HTTPException(500, f"Combine failed: {str(e)[:300]}")

    @app.post("/api/plugins/dashboard/discover")
    async def discover_tracks(data: dict):
        """Discover all available tracks in a chart file or CDLC.

        Body: {
            "chart_path": "/path/to/ch_folder_or_chart_file",  (optional)
            "cdlc_path": "/path/to/song.psarc",                (optional)
            "song_key": "..."                                   (optional, looks up from library)
        }

        Returns all sections/arrangements found with instrument guesses,
        so the user can manually assign which track is drums, vocals, etc.
        """
        lib = load_library()
        songs = lib.get("songs", {})

        song_key = data.get("song_key")
        chart_path = data.get("chart_path")
        cdlc_path = data.get("cdlc_path")

        # Look up from library if song_key provided
        if song_key and song_key in songs:
            song = songs[song_key]
            if not chart_path:
                chart_path = song.get("clonehero_path") or song.get("vocals_path")
            if not cdlc_path:
                cdlc_path = song.get("cdlc_path")

        try:
            import sys
            repo_root = str(Path(__file__).resolve().parent.parent.parent)
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from track_discovery import discover_all

            result = discover_all(
                chart_path=Path(chart_path) if chart_path else None,
                cdlc_path=Path(cdlc_path) if cdlc_path else None,
            )
            return result
        except ImportError as e:
            raise HTTPException(500, f"Cannot import track_discovery: {e}")
        except Exception as e:
            log.error(f"Discovery failed: {traceback.format_exc()}")
            raise HTTPException(500, f"Discovery failed: {str(e)[:300]}")

    @app.post("/api/plugins/dashboard/assign-tracks")
    async def assign_tracks(data: dict):
        """Save manual track assignments for a song.

        Body: {
            "song_key": "...",
            "assignments": {
                "drums": {"source": "chart", "section": "ExpertDrums"},
                "vocals": {"source": "chart", "section": "ExpertVocals"},
                "guitar": {"source": "cdlc", "arrangement": "Lead"},
                "rhythm": {"source": "cdlc", "arrangement": "Rhythm"},
                "bass": {"source": "cdlc", "arrangement": "Bass"}
            }
        }
        """
        lib = load_library()
        songs = lib.get("songs", {})
        song_key = data.get("song_key", "")
        assignments = data.get("assignments", {})

        if song_key not in songs:
            raise HTTPException(404, f"Song '{song_key}' not in library")

        songs[song_key]["track_assignments"] = assignments
        lib["songs"] = songs
        save_library(lib)

        return {"success": True, "assignments": assignments}

    @app.post("/api/plugins/dashboard/pair")
    async def pair_songs(data: dict):
        """Pair a CDLC song with a Clone Hero song in the library.

        Body: {"cdlc_key": "...", "clonehero_key": "..."}
        Merges the Clone Hero data into the CDLC entry.
        """
        lib = load_library()
        songs = lib.get("songs", {})

        cdlc_key = data.get("cdlc_key", "")
        ch_key = data.get("clonehero_key", "")

        if cdlc_key not in songs:
            raise HTTPException(404, f"CDLC song '{cdlc_key}' not found")
        if ch_key not in songs:
            raise HTTPException(404, f"Clone Hero song '{ch_key}' not found")

        # Merge CH data into the CDLC entry
        cdlc_song = songs[cdlc_key]
        ch_song = songs[ch_key]

        cdlc_song["clonehero_path"] = ch_song.get("clonehero_path")
        cdlc_song["has_drums"] = True

        # Remove the standalone CH entry since it's merged
        del songs[ch_key]

        lib["songs"] = songs
        save_library(lib)

        return {"success": True, "merged_key": cdlc_key}

    @app.get("/api/plugins/dashboard/stats")
    async def get_stats():
        """Library statistics."""
        lib = load_library()
        songs = lib.get("songs", {})

        total = len(songs)
        with_stems = sum(1 for s in songs.values() if s.get("has_stems"))
        with_drums = sum(1 for s in songs.values() if s.get("has_drums"))
        with_vocals = sum(1 for s in songs.values() if s.get("has_vocals"))
        with_cdlc = sum(1 for s in songs.values() if s.get("has_cdlc"))
        combined = sum(1 for s in songs.values() if s.get("combined"))
        missing_stems = sum(1 for s in songs.values()
                           if s.get("has_cdlc") and not s.get("has_stems"))

        return {
            "total": total,
            "with_stems": with_stems,
            "with_drums": with_drums,
            "with_vocals": with_vocals,
            "with_cdlc": with_cdlc,
            "combined": combined,
            "missing_stems": missing_stems,
            "est_cost": round(missing_stems * 0.021, 2),
        }

    @app.post("/api/plugins/dashboard/settings")
    async def save_settings(data: dict):
        """Save dashboard settings."""
        lib = load_library()
        settings = lib.get("settings", {})

        for key in ("cdlc_dir", "clonehero_dir", "stems_dir",
                     "replicate_api_key", "output_dir"):
            if key in data:
                settings[key] = data[key]

        # Set API key in environment if provided
        if settings.get("replicate_api_key"):
            os.environ["REPLICATE_API_TOKEN"] = settings["replicate_api_key"]

        lib["settings"] = settings
        save_library(lib)

        log.info("Dashboard settings saved")
        return {"saved": True}

    @app.get("/api/plugins/dashboard/settings")
    async def get_settings():
        """Load dashboard settings (API key masked)."""
        lib = load_library()
        settings = lib.get("settings", {})

        # Mask the API key for display
        masked = dict(settings)
        api_key = masked.get("replicate_api_key", "")
        if api_key and len(api_key) > 8:
            masked["replicate_api_key_masked"] = api_key[:4] + "..." + api_key[-4:]
            masked["has_api_key"] = True
        else:
            masked["replicate_api_key_masked"] = ""
            masked["has_api_key"] = bool(api_key)

        # Don't send raw key to frontend
        masked.pop("replicate_api_key", None)

        return masked

    @app.post("/api/plugins/dashboard/test-api")
    async def test_replicate_api():
        """Test the Replicate API connection."""
        api_key = os.environ.get("REPLICATE_API_TOKEN", "")
        if not api_key:
            lib = load_library()
            api_key = lib.get("settings", {}).get("replicate_api_key", "")
            if api_key:
                os.environ["REPLICATE_API_TOKEN"] = api_key

        if not api_key:
            return {"ok": False, "error": "No API key configured"}

        try:
            import replicate
            client = replicate.Client(api_token=api_key)
            # Simple API call to verify credentials
            _models = client.models.list()
            return {"ok": True, "message": "API key is valid"}
        except ImportError:
            return {"ok": False, "error": "replicate package not installed (pip install replicate)"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @app.post("/api/plugins/dashboard/auto-match")
    async def auto_match(data: dict = None):
        """Auto-match CDLC songs to Clone Hero songs by title similarity.

        Uses Levenshtein distance on normalized title+artist strings.
        Returns proposed pairings.
        """
        lib = load_library()
        songs = lib.get("songs", {})

        cdlc_songs = {k: v for k, v in songs.items() if v.get("has_cdlc") and not v.get("clonehero_path")}
        ch_songs = {k: v for k, v in songs.items() if v.get("clonehero_path") and not v.get("has_cdlc")}

        if not cdlc_songs or not ch_songs:
            return {"matches": [], "message": "Need both unmatched CDLC and Clone Hero songs."}

        def normalize(s):
            return s.lower().replace("_", " ").replace("-", " ").strip()

        def levenshtein(a, b):
            if len(a) < len(b):
                return levenshtein(b, a)
            if len(b) == 0:
                return len(a)
            prev = range(len(b) + 1)
            for i, ca in enumerate(a):
                curr = [i + 1]
                for j, cb in enumerate(b):
                    cost = 0 if ca == cb else 1
                    curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
                prev = curr
            return prev[len(b)]

        matches = []
        used_ch = set()

        for cdlc_key, cdlc_song in cdlc_songs.items():
            cdlc_str = normalize(f"{cdlc_song.get('artist', '')} {cdlc_song.get('title', '')}")
            best_match = None
            best_score = 999

            for ch_key, ch_song in ch_songs.items():
                if ch_key in used_ch:
                    continue
                ch_str = normalize(f"{ch_song.get('artist', '')} {ch_song.get('title', '')}")
                dist = levenshtein(cdlc_str, ch_str)
                max_len = max(len(cdlc_str), len(ch_str), 1)
                similarity = 1.0 - (dist / max_len)

                if similarity > 0.5 and dist < best_score:
                    best_score = dist
                    best_match = {
                        "cdlc_key": cdlc_key,
                        "clonehero_key": ch_key,
                        "cdlc_title": f"{cdlc_song.get('artist', '')} - {cdlc_song.get('title', '')}",
                        "ch_title": f"{ch_song.get('artist', '')} - {ch_song.get('title', '')}",
                        "similarity": round(similarity, 3),
                    }

            if best_match:
                matches.append(best_match)
                used_ch.add(best_match["clonehero_key"])

        # Sort by similarity descending
        matches.sort(key=lambda m: m["similarity"], reverse=True)

        return {"matches": matches, "count": len(matches)}
