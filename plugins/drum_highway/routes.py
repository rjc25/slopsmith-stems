"""Drum Highway plugin — backend routes.

Serves parsed Clone Hero drum chart data for the highway renderer.
"""

import json
from pathlib import Path

from fastapi import HTTPException


def setup(app, context):
    config_dir = Path(context["config_dir"])
    log = context["log"]

    @app.get("/api/plugins/drum_highway/chart/{song_key}")
    async def get_drum_chart(song_key: str):
        """Return parsed drum chart data for a song.

        Looks for chart_data.json in the combined package's drums/ folder.
        """
        # Check stem_toggle index for the song's location
        stem_index_path = config_dir / "stem_index.json"
        if stem_index_path.exists():
            index = json.loads(stem_index_path.read_text())
            if song_key in index:
                stems_dir = Path(index[song_key].get("stems_dir", ""))
                # Look for drums chart in parent or sibling directory
                for search_dir in [stems_dir.parent, stems_dir.parent / "drums", stems_dir / ".." / "drums"]:
                    chart_file = search_dir / "chart_data.json"
                    if chart_file.exists():
                        return json.loads(chart_file.read_text())

        # Also check the combined packages directory
        combined_dir = config_dir / "combined"
        if combined_dir.exists():
            song_dir = combined_dir / song_key / "drums"
            chart_file = song_dir / "chart_data.json"
            if chart_file.exists():
                return json.loads(chart_file.read_text())

        raise HTTPException(404, f"No drum chart found for '{song_key}'")

    @app.get("/api/plugins/drum_highway/songs")
    async def list_drum_songs():
        """List all songs that have drum charts available."""
        songs = []

        combined_dir = config_dir / "combined"
        if combined_dir.exists():
            for song_dir in combined_dir.iterdir():
                chart_file = song_dir / "drums" / "chart_data.json"
                if chart_file.exists():
                    try:
                        data = json.loads(chart_file.read_text())
                        meta = data.get("metadata", {})
                        difficulties = list(data.get("drums", {}).keys())
                        songs.append({
                            "key": song_dir.name,
                            "name": meta.get("Name", meta.get("name", song_dir.name)),
                            "artist": meta.get("Artist", meta.get("artist", "")),
                            "difficulties": difficulties,
                        })
                    except json.JSONDecodeError:
                        continue

        return {"songs": songs, "count": len(songs)}
