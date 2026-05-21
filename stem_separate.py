#!/usr/bin/env python3
"""Separate a single audio file into stems using Demucs via Replicate API."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

import replicate
import requests
from tqdm import tqdm


DEMUCS_MODEL = "cjwbw/demucs:25a173108cff36ef9f80f854c162d01df9e6528be175794b81571f6e0feea7e1"
STEMS = ["drums", "bass", "vocals", "guitar", "other"]


def extract_audio_from_psarc(psarc_path: Path, output_dir: Path) -> Path:
    """Extract the main audio track from a PSARC archive.

    Tries multiple methods:
    1. If psarc contains .ogg files, extract directly
    2. If .wem files, convert via ffmpeg/vgmstream
    3. Fallback: try ffmpeg directly on the archive
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_out = output_dir / "original.mp3"

    if audio_out.exists():
        print(f"  Audio already extracted: {audio_out}")
        return audio_out

    # Try extracting with unpsarc or python psarc library
    temp_dir = Path(tempfile.mkdtemp())

    try:
        # Method 1: Try to find audio files by examining the PSARC
        # PSARC files contain .wem (Wwise audio) or .ogg files
        # We'll use ffmpeg to probe and convert whatever we find

        # First, try treating the psarc as containing ogg directly
        # Some CDLC tools extract to a folder structure
        extracted_dir = output_dir / "extracted"

        # Check if there's already an extracted audio file nearby
        psarc_dir = psarc_path.parent
        song_name = psarc_path.stem.replace("_p", "").replace("_m", "")

        # Look for common audio formats near the PSARC
        for ext in [".ogg", ".wem", ".mp3", ".wav"]:
            candidates = list(psarc_dir.glob(f"*{ext}")) + list(psarc_dir.glob(f"**/*{ext}"))
            for candidate in candidates:
                if song_name.lower() in candidate.stem.lower() or "song" in candidate.stem.lower():
                    print(f"  Found audio: {candidate}")
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", str(candidate), "-q:a", "2", str(audio_out)],
                        capture_output=True, timeout=60
                    )
                    if audio_out.exists() and audio_out.stat().st_size > 10000:
                        return audio_out

        # Method 2: Try ffprobe/ffmpeg directly on the PSARC
        # Some versions of ffmpeg can read inside certain archive formats
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(psarc_path), "-q:a", "2", str(audio_out)],
            capture_output=True, text=True, timeout=60
        )
        if audio_out.exists() and audio_out.stat().st_size > 10000:
            return audio_out

        # Method 3: Manual PSARC extraction
        # PSARC is a proprietary Sony archive format
        # We need to parse the header and extract entries
        print(f"  Attempting PSARC binary extraction...")
        audio_data = extract_audio_from_psarc_binary(psarc_path, temp_dir)
        if audio_data:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(audio_data), "-q:a", "2", str(audio_out)],
                capture_output=True, timeout=60
            )
            if audio_out.exists() and audio_out.stat().st_size > 10000:
                return audio_out

        raise FileNotFoundError(
            f"Could not extract audio from {psarc_path}. "
            "You may need to extract the audio manually using the Rocksmith Custom Song Toolkit "
            "or provide a pre-extracted .ogg/.mp3 file."
        )
    finally:
        # Cleanup temp files
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


def extract_audio_from_psarc_binary(psarc_path: Path, temp_dir: Path):
    """Try to extract audio by parsing PSARC binary format.

    PSARC header: magic (4) + version (4) + compression (4) + toc_length (4) +
                  toc_entry_size (4) + toc_entry_count (4) + block_size (4)
    """
    import struct
    import zlib

    with open(psarc_path, "rb") as f:
        magic = f.read(4)
        if magic != b"PSAR":
            return None

        version = struct.unpack(">I", f.read(4))[0]
        compression = f.read(4)
        toc_length = struct.unpack(">I", f.read(4))[0]
        toc_entry_size = struct.unpack(">I", f.read(4))[0]
        toc_entry_count = struct.unpack(">I", f.read(4))[0]
        block_size = struct.unpack(">I", f.read(4))[0]

        if block_size == 0:
            block_size = 65536

        # Read TOC entries
        entries = []
        for i in range(toc_entry_count):
            entry_data = f.read(toc_entry_size)
            if len(entry_data) < 30:
                continue
            # MD5 hash (16) + block_index (4) + uncompressed_size (5) + file_offset (5)
            md5 = entry_data[:16]
            block_index = struct.unpack(">I", entry_data[16:20])[0]
            # 40-bit (5 byte) sizes
            uncomp_size = int.from_bytes(entry_data[20:25], "big")
            file_offset = int.from_bytes(entry_data[25:30], "big")
            entries.append({
                "block_index": block_index,
                "size": uncomp_size,
                "offset": file_offset,
            })

        if not entries:
            return None

        # First entry is the file listing
        # Read file names from first entry
        f.seek(entries[0]["offset"])
        try:
            name_data = zlib.decompress(f.read(min(entries[0]["size"] * 2, 65536)))
            names = name_data.decode("utf-8", errors="replace").split("\n")
        except Exception:
            names = []

        # Find audio file (usually .wem or .ogg)
        audio_entry_idx = None
        audio_name = None
        for idx, name in enumerate(names):
            if name.endswith((".wem", ".ogg", ".opus")):
                audio_entry_idx = idx + 1  # +1 because entry 0 is the file listing
                audio_name = name
                break

        if audio_entry_idx is None or audio_entry_idx >= len(entries):
            return None

        # Extract the audio file
        entry = entries[audio_entry_idx]
        f.seek(entry["offset"])

        ext = Path(audio_name).suffix if audio_name else ".wem"
        out_path = temp_dir / f"extracted_audio{ext}"

        # Read and decompress blocks
        remaining = entry["size"]
        with open(out_path, "wb") as out_f:
            while remaining > 0:
                # Read block size (2 bytes)
                block_header = f.read(2)
                if len(block_header) < 2:
                    break
                block_len = struct.unpack(">H", block_header)[0]

                if block_len == 0:
                    # Uncompressed block
                    chunk = f.read(min(block_size, remaining))
                else:
                    compressed = f.read(block_len)
                    try:
                        chunk = zlib.decompress(compressed)
                    except zlib.error:
                        chunk = compressed

                out_f.write(chunk)
                remaining -= len(chunk)

        if out_path.exists() and out_path.stat().st_size > 1000:
            return out_path

    return None


def separate_stems(audio_path: Path, output_dir: Path, model: str = DEMUCS_MODEL) -> dict:
    """Send audio to Demucs via Replicate and download stems."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check if already processed
    existing = {s: output_dir / f"{s}.mp3" for s in STEMS}
    if all(p.exists() for p in existing.values()):
        print(f"  Stems already exist, skipping separation")
        return existing

    print(f"  Uploading to Demucs...")

    with open(audio_path, "rb") as f:
        output = replicate.run(
            model,
            input={
                "audio": f,
                "stem": "none",  # Return all stems
            }
        )

    # Download each stem
    stem_paths = {}
    for stem_url in output:
        # Determine which stem this is from the URL or filename
        url_lower = str(stem_url).lower()
        stem_name = None
        for s in STEMS:
            if s in url_lower:
                stem_name = s
                break

        if stem_name is None:
            # Try to guess from position
            continue

        stem_path = output_dir / f"{stem_name}.mp3"
        print(f"  Downloading {stem_name}...")

        response = requests.get(stem_url, stream=True)
        response.raise_for_status()
        with open(stem_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        stem_paths[stem_name] = stem_path

    return stem_paths


def create_backing_track(stem_dir: Path, exclude: list[str] = None):
    """Mix stems together, excluding specified instruments.

    Default: creates a no-guitar backing track.
    """
    if exclude is None:
        exclude = ["guitar"]

    stems_to_mix = []
    for s in STEMS:
        if s not in exclude:
            stem_path = stem_dir / f"{s}.mp3"
            if stem_path.exists():
                stems_to_mix.append(stem_path)

    if not stems_to_mix:
        print("  No stems to mix")
        return None

    exclude_str = "_no_" + "_".join(exclude)
    output_path = stem_dir / f"backing{exclude_str}.mp3"

    if output_path.exists():
        print(f"  Backing track already exists: {output_path}")
        return output_path

    # Use ffmpeg to mix stems
    filter_parts = []
    inputs = []
    for i, stem in enumerate(stems_to_mix):
        inputs.extend(["-i", str(stem)])
        filter_parts.append(f"[{i}:a]")

    filter_str = "".join(filter_parts) + f"amix=inputs={len(stems_to_mix)}:duration=longest[out]"

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_str,
        "-map", "[out]",
        "-q:a", "2",
        str(output_path)
    ]

    print(f"  Creating backing track ({', '.join(exclude)} removed)...")
    subprocess.run(cmd, capture_output=True, timeout=120)

    if output_path.exists():
        return output_path
    return None


def process_song(input_path: Path, output_dir: Path):
    """Full pipeline: extract audio, separate stems, create backing track."""
    song_name = input_path.stem.replace("_p", "").replace("_m", "")
    song_output = output_dir / song_name
    song_output.mkdir(parents=True, exist_ok=True)

    print(f"\nProcessing: {song_name}")

    # Step 1: Extract audio
    if input_path.suffix.lower() == ".psarc":
        audio_path = extract_audio_from_psarc(input_path, song_output)
    elif input_path.suffix.lower() in (".mp3", ".ogg", ".wav", ".flac"):
        audio_path = input_path
    else:
        print(f"  Unsupported format: {input_path.suffix}")
        return None

    # Step 2: Separate stems
    stems = separate_stems(audio_path, song_output)

    # Step 3: Create backing tracks
    create_backing_track(song_output, exclude=["guitar"])
    create_backing_track(song_output, exclude=["vocals"])
    create_backing_track(song_output, exclude=["guitar", "vocals"])

    print(f"  Done: {song_name}")
    return stems


def main():
    parser = argparse.ArgumentParser(description="Separate a song into stems using Demucs")
    parser.add_argument("--input", "-i", required=True, help="Input audio file or .psarc")
    parser.add_argument("--output", "-o", required=True, help="Output directory for stems")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)

    if not input_path.exists():
        print(f"Error: {input_path} not found")
        return 1

    if not os.getenv("REPLICATE_API_TOKEN"):
        print("Error: REPLICATE_API_TOKEN not set")
        print("Get your token at: https://replicate.com/account/api-tokens")
        return 1

    process_song(input_path, output_dir)
    return 0


if __name__ == "__main__":
    exit(main())
