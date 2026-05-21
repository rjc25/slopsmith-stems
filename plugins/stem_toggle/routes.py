"""Stem Toggle plugin — backend routes.

Serves individual stem audio files and manages the stem index
that maps songs to their separated stems.
"""

import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse


def setup(app, context):
    config_dir = Path(context["config_dir"])
    log = context["log"]
    stem_index_path = config_dir / "stem_index.json"

    def load_index():
        if stem_index_path.exists():
            return json.loads(stem_index_path.read_text())
        return {}

    def save_index(index):
        stem_index_path.write_text(json.dumps(index, indent=2))

    @app.get("/api/plugins/stem_toggle/stems/{song_key}")
    async def get_song_stems(song_key: str):
        """Return available stems for a song."""
        index = load_index()
        if song_key not in index:
            return {"stems": [], "has_stems": False}

        song_entry = index[song_key]
        stems = []
        for stem_name, stem_path in song_entry.get("stems", {}).items():
            if Path(stem_path).exists():
                stems.append({
                    "name": stem_name,
                    "url": f"/api/plugins/stem_toggle/audio/{song_key}/{stem_name}",
                })

        return {"stems": stems, "has_stems": len(stems) > 0}

    @app.get("/api/plugins/stem_toggle/audio/{song_key}/{stem_name}")
    async def get_stem_audio(song_key: str, stem_name: str):
        """Serve a specific stem audio file."""
        index = load_index()
        if song_key not in index:
            raise HTTPException(404, "Song not found in stem index")

        stem_path = index[song_key].get("stems", {}).get(stem_name)
        if not stem_path or not Path(stem_path).exists():
            raise HTTPException(404, f"Stem '{stem_name}' not found")

        return FileResponse(
            stem_path,
            media_type="audio/mpeg",
            headers={"Accept-Ranges": "bytes"},
        )

    @app.post("/api/plugins/stem_toggle/index")
    async def update_stem_index(data: dict):
        """Register stems for a song.

        Body: {"song_key": "...", "stems_dir": "/path/to/stems/"}
        Scans the directory for drum.mp3, bass.mp3, etc.
        """
        song_key = data.get("song_key")
        stems_dir = Path(data.get("stems_dir", ""))

        if not song_key or not stems_dir.exists():
            raise HTTPException(400, "Invalid song_key or stems_dir")

        stem_names = ["drums", "bass", "vocals", "guitar", "other"]
        found_stems = {}
        for name in stem_names:
            for ext in [".mp3", ".ogg", ".wav", ".flac"]:
                stem_file = stems_dir / f"{name}{ext}"
                if stem_file.exists():
                    found_stems[name] = str(stem_file)
                    break

        index = load_index()
        index[song_key] = {
            "stems_dir": str(stems_dir),
            "stems": found_stems,
        }
        save_index(index)

        log.info(f"Indexed {len(found_stems)} stems for {song_key}")
        return {"indexed": len(found_stems), "stems": list(found_stems.keys())}

    @app.post("/api/plugins/stem_toggle/scan")
    async def scan_stems_directory(data: dict):
        """Scan a directory tree for stem folders and auto-index all songs.

        Body: {"base_dir": "/path/to/all/stems/"}
        Expects structure: base_dir/song_name/drums.mp3, bass.mp3, etc.
        """
        base_dir = Path(data.get("base_dir", ""))
        if not base_dir.exists():
            raise HTTPException(400, "base_dir does not exist")

        index = load_index()
        indexed = 0

        for song_dir in sorted(base_dir.iterdir()):
            if not song_dir.is_dir():
                continue

            stem_names = ["drums", "bass", "vocals", "guitar", "other"]
            found_stems = {}
            for name in stem_names:
                for ext in [".mp3", ".ogg", ".wav", ".flac"]:
                    stem_file = song_dir / f"{name}{ext}"
                    if stem_file.exists():
                        found_stems[name] = str(stem_file)
                        break

            if found_stems:
                song_key = song_dir.name
                index[song_key] = {
                    "stems_dir": str(song_dir),
                    "stems": found_stems,
                }
                indexed += 1

        save_index(index)
        log.info(f"Scanned {base_dir}: indexed {indexed} songs with stems")
        return {"scanned": indexed, "total_indexed": len(index)}

    @app.get("/api/plugins/stem_toggle/stats")
    async def stem_stats():
        """Return stats about the stem library."""
        index = load_index()
        return {
            "total_songs": len(index),
            "stem_counts": {
                name: sum(1 for s in index.values() if name in s.get("stems", {}))
                for name in ["drums", "bass", "vocals", "guitar", "other"]
            },
        }
