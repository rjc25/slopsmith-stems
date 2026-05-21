#!/usr/bin/env python3
"""Batch process an entire CDLC library through Demucs stem separation."""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from stem_separate import process_song


def find_songs(library_dir: Path, extensions: tuple = (".psarc", ".mp3", ".ogg", ".wav", ".flac")) -> list[Path]:
    """Find all processable song files in the library directory."""
    songs = []
    for ext in extensions:
        songs.extend(library_dir.rglob(f"*{ext}"))
    # Deduplicate by stem name (some CDLC have _p and _m variants)
    seen = set()
    unique = []
    for song in sorted(songs):
        key = song.stem.replace("_p", "").replace("_m", "").lower()
        if key not in seen:
            seen.add(key)
            unique.append(song)
    return unique


def load_progress(progress_file: Path) -> dict:
    """Load processing progress from file."""
    if progress_file.exists():
        return json.loads(progress_file.read_text())
    return {"completed": [], "failed": [], "skipped": []}


def save_progress(progress_file: Path, progress: dict):
    """Save processing progress to file."""
    progress_file.write_text(json.dumps(progress, indent=2))


def process_with_retry(song_path: Path, output_dir: Path, max_retries: int = 3) -> tuple[str, bool, str]:
    """Process a song with retries on failure."""
    song_name = song_path.stem
    for attempt in range(max_retries):
        try:
            result = process_song(song_path, output_dir)
            if result:
                return song_name, True, ""
            return song_name, False, "No stems returned"
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 30
                print(f"  Retry {attempt + 1}/{max_retries} for {song_name} in {wait}s: {e}")
                time.sleep(wait)
            else:
                return song_name, False, str(e)
    return song_name, False, "Max retries exceeded"


def main():
    parser = argparse.ArgumentParser(description="Batch process CDLC library through Demucs")
    parser.add_argument("--library", "-l", required=True, help="Path to CDLC library folder")
    parser.add_argument("--output", "-o", required=True, help="Output directory for stems")
    parser.add_argument("--workers", "-w", type=int, default=3, help="Parallel workers (default: 3)")
    parser.add_argument("--dry-run", action="store_true", help="List songs without processing")
    parser.add_argument("--resume", action="store_true", help="Resume from previous progress")
    args = parser.parse_args()

    library_dir = Path(args.library)
    output_dir = Path(args.output)
    progress_file = output_dir / "progress.json"

    if not library_dir.exists():
        print(f"Error: Library not found: {library_dir}")
        return 1

    if not os.getenv("REPLICATE_API_TOKEN"):
        print("Error: REPLICATE_API_TOKEN not set")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    # Find all songs
    songs = find_songs(library_dir)
    print(f"Found {len(songs)} songs in {library_dir}")

    if args.dry_run:
        for song in songs:
            print(f"  {song.name}")
        est_cost = len(songs) * 0.021
        est_time = len(songs) * 94 / 60
        print(f"\nEstimated cost: ${est_cost:.2f}")
        print(f"Estimated time: {est_time:.0f} minutes ({est_time/60:.1f} hours)")
        print(f"With {args.workers} workers: {est_time/args.workers:.0f} minutes")
        return 0

    # Load progress for resume
    progress = load_progress(progress_file) if args.resume else {"completed": [], "failed": [], "skipped": []}

    # Filter already completed
    remaining = [s for s in songs if s.stem not in progress["completed"]]
    print(f"Remaining: {remaining} ({len(songs) - len(remaining)} already done)")

    if not remaining:
        print("All songs already processed!")
        return 0

    # Process
    est_cost = len(remaining) * 0.021
    print(f"Processing {len(remaining)} songs (~${est_cost:.2f}, ~{len(remaining) * 94 / 60 / args.workers:.0f} min with {args.workers} workers)")

    completed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_with_retry, song, output_dir): song
            for song in remaining
        }

        with tqdm(total=len(remaining), desc="Processing") as pbar:
            for future in as_completed(futures):
                song_name, success, error = future.result()
                if success:
                    progress["completed"].append(song_name)
                    completed += 1
                else:
                    progress["failed"].append({"name": song_name, "error": error})
                    failed += 1

                pbar.update(1)
                pbar.set_postfix(ok=completed, fail=failed)

                # Save progress periodically
                if (completed + failed) % 10 == 0:
                    save_progress(progress_file, progress)

    save_progress(progress_file, progress)

    print(f"\nDone! Completed: {completed}, Failed: {failed}")
    if failed > 0:
        print("Failed songs:")
        for f in progress["failed"][-10:]:
            print(f"  {f['name']}: {f['error']}")

    return 0


if __name__ == "__main__":
    exit(main())
