#!/usr/bin/env python3
"""Create backing tracks from separated stems.

Generates various mixes with instruments removed for practice:
- no_guitar: Practice guitar along with the band
- no_vocals: Sing along / karaoke mode
- no_guitar_no_vocals: Pure instrumental practice
- drums_only: Rhythm practice
- guitar_only: Learn the guitar part by ear
"""

import argparse
import subprocess
from pathlib import Path

from tqdm import tqdm

STEMS = ["drums", "bass", "vocals", "guitar", "other"]

PRESETS = {
    "no_guitar": {"exclude": ["guitar"], "desc": "Practice guitar (guitar removed)"},
    "no_vocals": {"exclude": ["vocals"], "desc": "Karaoke mode (vocals removed)"},
    "no_guitar_no_vocals": {"exclude": ["guitar", "vocals"], "desc": "Instrumental only"},
    "drums_only": {"include": ["drums"], "desc": "Rhythm practice"},
    "guitar_only": {"include": ["guitar"], "desc": "Guitar part isolated"},
    "bass_only": {"include": ["bass"], "desc": "Bass part isolated"},
}


def mix_stems(stem_dir: Path, preset_name: str, preset: dict) -> Path:
    """Mix stems according to preset rules."""
    output_path = stem_dir / f"{preset_name}.mp3"
    if output_path.exists():
        return output_path

    if "include" in preset:
        stems_to_use = preset["include"]
    else:
        stems_to_use = [s for s in STEMS if s not in preset.get("exclude", [])]

    stem_files = []
    for s in stems_to_use:
        p = stem_dir / f"{s}.mp3"
        if p.exists():
            stem_files.append(p)

    if not stem_files:
        return None

    if len(stem_files) == 1:
        # Just copy the single stem
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(stem_files[0]), "-q:a", "2", str(output_path)],
            capture_output=True, timeout=60
        )
    else:
        # Mix multiple stems
        inputs = []
        filter_parts = []
        for i, sf in enumerate(stem_files):
            inputs.extend(["-i", str(sf)])
            filter_parts.append(f"[{i}:a]")

        filter_str = "".join(filter_parts) + f"amix=inputs={len(stem_files)}:duration=longest[out]"

        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_str,
            "-map", "[out]",
            "-q:a", "2",
            str(output_path)
        ]
        subprocess.run(cmd, capture_output=True, timeout=120)

    return output_path if output_path.exists() else None


def process_song_dir(song_dir: Path, presets: list[str] = None):
    """Create all backing track presets for a song directory."""
    if presets is None:
        presets = list(PRESETS.keys())

    # Verify stems exist
    has_stems = any((song_dir / f"{s}.mp3").exists() for s in STEMS)
    if not has_stems:
        return False

    for preset_name in presets:
        if preset_name in PRESETS:
            mix_stems(song_dir, preset_name, PRESETS[preset_name])

    return True


def main():
    parser = argparse.ArgumentParser(description="Create backing tracks from separated stems")
    parser.add_argument("--stems-dir", "-s", required=True, help="Directory containing song stem folders")
    parser.add_argument("--presets", "-p", nargs="+", choices=list(PRESETS.keys()),
                        default=list(PRESETS.keys()), help="Which backing tracks to create")
    args = parser.parse_args()

    stems_dir = Path(args.stems_dir)
    if not stems_dir.exists():
        print(f"Error: {stems_dir} not found")
        return 1

    # Find all song directories (those with stem files)
    song_dirs = []
    for d in stems_dir.iterdir():
        if d.is_dir() and any((d / f"{s}.mp3").exists() for s in STEMS):
            song_dirs.append(d)

    print(f"Found {len(song_dirs)} songs with stems")
    print(f"Creating presets: {', '.join(args.presets)}")

    for song_dir in tqdm(song_dirs, desc="Creating backing tracks"):
        process_song_dir(song_dir, args.presets)

    print("Done!")
    return 0


if __name__ == "__main__":
    exit(main())
