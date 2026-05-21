#!/usr/bin/env python3
"""Manually combine CDLC (guitar/bass), Clone Hero/YARC (drums), and Demucs stems
into a unified SlopSmith package.

Usage:
    python combine_charts.py \
        --cdlc /path/to/song.psarc \
        --clonehero /path/to/clonehero/song_folder/ \
        --stems /path/to/stems/song/ \
        --output /path/to/combined/song/

The Clone Hero folder should contain:
    notes.chart (or notes.mid)
    song.ogg (or song.mp3)
    song.ini (metadata)

The stems folder should contain:
    drums.mp3, bass.mp3, vocals.mp3, guitar.mp3, other.mp3
"""

import argparse
import configparser
import json
import re
import shutil
from pathlib import Path


# ── Clone Hero .chart Parser (Drums) ──

# Clone Hero drum note mappings (Expert difficulty)
# .chart files use MIDI-like note numbers for drums
CH_DRUM_NOTES = {
    0: {"name": "kick", "color": "#FF8800", "lane": 0},
    1: {"name": "red", "color": "#FF0000", "lane": 1},       # Snare
    2: {"name": "yellow", "color": "#FFFF00", "lane": 2},     # Hi-hat / Yellow cymbal
    3: {"name": "blue", "color": "#0088FF", "lane": 3},       # Tom / Blue cymbal
    4: {"name": "orange", "color": "#FF8800", "lane": 4},     # 5-lane cymbal (optional)
    5: {"name": "green", "color": "#00FF00", "lane": 5},      # Floor tom / Green cymbal
}

# Pro drums cymbal markers (Clone Hero uses note 66-68 as cymbal flags)
CH_CYMBAL_FLAGS = {66: 2, 67: 3, 68: 5}  # Maps to yellow, blue, green


def parse_chart_file(chart_path: Path) -> dict:
    """Parse a Clone Hero .chart file and extract drum track data.

    Returns: {
        "metadata": { "Name": ..., "Artist": ..., "Resolution": ..., ... },
        "sync_track": [ { "tick": int, "bpm": float }, ... ],
        "drums": {
            "expert": [ { "tick": int, "note": int, "duration": int, "name": str, "color": str, "lane": int }, ... ],
            "hard": [ ... ],
            "medium": [ ... ],
            "easy": [ ... ],
        }
    }
    """
    text = chart_path.read_text(encoding="utf-8", errors="replace")

    result = {
        "metadata": {},
        "sync_track": [],
        "drums": {"expert": [], "hard": [], "medium": [], "easy": []},
    }

    # Parse sections
    current_section = None
    for line in text.split("\n"):
        line = line.strip()

        # Section headers
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            continue

        if line in ("{", "}"):
            continue

        # Song metadata
        if current_section == "Song":
            match = re.match(r'(\w+)\s*=\s*"?(.+?)"?\s*$', line)
            if match:
                result["metadata"][match.group(1)] = match.group(2)

        # Sync track (BPM changes and time signatures)
        elif current_section == "SyncTrack":
            match = re.match(r"(\d+)\s*=\s*B\s+(\d+)", line)
            if match:
                tick = int(match.group(1))
                bpm = int(match.group(2)) / 1000.0  # Stored as BPM * 1000
                result["sync_track"].append({"tick": tick, "bpm": bpm})

            match = re.match(r"(\d+)\s*=\s*TS\s+(\d+)(?:\s+(\d+))?", line)
            if match:
                tick = int(match.group(1))
                numerator = int(match.group(2))
                denominator = 2 ** int(match.group(3) or 2)
                result["sync_track"].append({
                    "tick": tick,
                    "ts_numerator": numerator,
                    "ts_denominator": denominator,
                })

        # Drum tracks
        elif "Drums" in (current_section or ""):
            difficulty = None
            if "ExpertDrums" in current_section:
                difficulty = "expert"
            elif "HardDrums" in current_section:
                difficulty = "hard"
            elif "MediumDrums" in current_section:
                difficulty = "medium"
            elif "EasyDrums" in current_section:
                difficulty = "easy"

            if difficulty:
                match = re.match(r"(\d+)\s*=\s*N\s+(\d+)\s+(\d+)", line)
                if match:
                    tick = int(match.group(1))
                    note = int(match.group(2))
                    duration = int(match.group(3))

                    drum_info = CH_DRUM_NOTES.get(note)
                    if drum_info:
                        result["drums"][difficulty].append({
                            "tick": tick,
                            "note": note,
                            "duration": duration,
                            **drum_info,
                        })

    return result


def parse_song_ini(ini_path: Path) -> dict:
    """Parse Clone Hero song.ini metadata."""
    config = configparser.ConfigParser()
    config.read(str(ini_path), encoding="utf-8")
    meta = {}
    if "song" in config:
        for key in config["song"]:
            meta[key] = config["song"][key]
    elif "Song" in config:
        for key in config["Song"]:
            meta[key] = config["Song"][key]
    return meta


def ticks_to_seconds(tick: int, sync_track: list, resolution: int = 192) -> float:
    """Convert chart ticks to seconds using BPM data."""
    if not sync_track:
        return tick / resolution * 0.5  # Default 120 BPM

    bpm_events = [e for e in sync_track if "bpm" in e]
    if not bpm_events:
        return tick / resolution * 0.5

    seconds = 0.0
    last_tick = 0
    last_bpm = bpm_events[0]["bpm"] if bpm_events else 120.0

    for event in bpm_events:
        if event["tick"] > tick:
            break
        # Time for ticks between last event and this one
        delta_ticks = event["tick"] - last_tick
        seconds += (delta_ticks / resolution) * (60.0 / last_bpm)
        last_tick = event["tick"]
        last_bpm = event["bpm"]

    # Remaining ticks after last BPM change
    delta_ticks = tick - last_tick
    seconds += (delta_ticks / resolution) * (60.0 / last_bpm)

    return seconds


def convert_drums_to_timed(chart_data: dict, difficulty: str = "expert") -> list:
    """Convert tick-based drum notes to time-based (seconds)."""
    resolution = int(chart_data["metadata"].get("Resolution", 192))
    sync_track = chart_data["sync_track"]
    drum_notes = chart_data["drums"].get(difficulty, [])

    timed_notes = []
    for note in drum_notes:
        time_sec = ticks_to_seconds(note["tick"], sync_track, resolution)
        timed_notes.append({
            "time": round(time_sec, 4),
            "note": note["note"],
            "name": note["name"],
            "color": note["color"],
            "lane": note["lane"],
            "duration": note["duration"],
        })

    return sorted(timed_notes, key=lambda n: n["time"])


# ── Combiner ──

def parse_vocals_from_chart(chart_path: Path, sync_track: list = None, resolution: int = 192) -> dict:
    """Extract vocal data (lyrics + pitch) from a .chart file's [ExpertVocals] section."""
    text = chart_path.read_text(encoding="utf-8", errors="replace")

    vocals = {"lyrics": [], "pitch_events": []}
    current_section = None

    for line in text.split("\n"):
        line = line.strip()

        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            continue

        if line in ("{", "}"):
            continue

        # Look for vocal sections
        if current_section and "Vocals" in current_section:
            # Lyric events: tick = E "lyric text"
            lyric_match = re.match(r'(\d+)\s*=\s*E\s+"([^"]*)"', line)
            if lyric_match:
                tick = int(lyric_match.group(1))
                lyric_text = lyric_match.group(2)
                time_sec = ticks_to_seconds(tick, sync_track or [], resolution)
                vocals["lyrics"].append({
                    "tick": tick,
                    "time": round(time_sec, 4),
                    "text": lyric_text,
                })

            # Pitch events: tick = N <midi_note> <duration>
            note_match = re.match(r'(\d+)\s*=\s*N\s+(\d+)\s+(\d+)', line)
            if note_match:
                tick = int(note_match.group(1))
                midi_note = int(note_match.group(2))
                duration_ticks = int(note_match.group(3))
                time_sec = ticks_to_seconds(tick, sync_track or [], resolution)
                end_sec = ticks_to_seconds(tick + duration_ticks, sync_track or [], resolution)

                # MIDI note to name
                note_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
                octave = (midi_note // 12) - 1
                note_name = note_names[midi_note % 12]

                vocals["pitch_events"].append({
                    "tick": tick,
                    "time": round(time_sec, 4),
                    "duration": round(end_sec - time_sec, 4),
                    "midi_note": midi_note,
                    "note_name": f"{note_name}{octave}",
                    "frequency": round(440.0 * (2 ** ((midi_note - 69) / 12)), 2),
                })

    # Match lyrics to pitch events by closest tick
    for lyric in vocals["lyrics"]:
        closest = None
        closest_dist = float("inf")
        for pe in vocals["pitch_events"]:
            dist = abs(pe["tick"] - lyric["tick"])
            if dist < closest_dist:
                closest = pe
                closest_dist = dist
        if closest and closest_dist < 50:  # Within ~50 ticks
            lyric["midi_note"] = closest["midi_note"]
            lyric["note_name"] = closest["note_name"]

    return vocals


def combine(cdlc_path: Path, ch_path: Path, stems_path: Path, output_path: Path,
            vocals_path: Path = None):
    """Combine CDLC, Clone Hero charts, vocals, and stems into unified package."""
    output_path.mkdir(parents=True, exist_ok=True)

    manifest = {
        "format": "slopsmith-combined",
        "version": "1.0.0",
        "sources": {},
    }

    # 1. Copy CDLC (guitar/bass source)
    if cdlc_path and cdlc_path.exists():
        cdlc_dest = output_path / "cdlc"
        cdlc_dest.mkdir(exist_ok=True)
        if cdlc_path.is_file():
            shutil.copy2(cdlc_path, cdlc_dest / cdlc_path.name)
        else:
            shutil.copytree(cdlc_path, cdlc_dest, dirs_exist_ok=True)
        manifest["sources"]["cdlc"] = str(cdlc_path.name)
        print(f"  CDLC: {cdlc_path.name}")

    # 2. Parse and store Clone Hero drum chart
    if ch_path and ch_path.exists():
        chart_file = None
        for name in ["notes.chart", "Notes.chart", "notes.mid", "Notes.mid"]:
            candidate = ch_path / name
            if candidate.exists():
                chart_file = candidate
                break

        if chart_file and chart_file.suffix == ".chart":
            chart_data = parse_chart_file(chart_file)
            ini_meta = {}
            ini_path = ch_path / "song.ini"
            if ini_path.exists():
                ini_meta = parse_song_ini(ini_path)

            # Convert drum notes to timed format
            drums_timed = {}
            for diff in ["expert", "hard", "medium", "easy"]:
                notes = convert_drums_to_timed(chart_data, diff)
                if notes:
                    drums_timed[diff] = notes
                    print(f"  Drums ({diff}): {len(notes)} notes")

            # Auto-sync: align CH chart timing to CDLC/stems audio
            sync_offset = 0.0
            ch_audio = None
            ref_audio = None

            # Find CH audio
            for ext in [".ogg", ".mp3", ".wav", ".opus"]:
                candidate = ch_path / f"song{ext}"
                if candidate.exists():
                    ch_audio = candidate
                    break

            # Find reference audio (prefer stems full mix, then CDLC extracted)
            if stems_path:
                for ext in [".mp3", ".ogg", ".wav"]:
                    candidate = stems_path / f"original{ext}"
                    if candidate.exists():
                        ref_audio = candidate
                        break
                # Try guitar stem as fallback reference
                if not ref_audio:
                    for ext in [".mp3", ".ogg", ".wav"]:
                        candidate = stems_path / f"guitar{ext}"
                        if candidate.exists():
                            ref_audio = candidate
                            break

            if ch_audio and ref_audio:
                try:
                    from audio_sync import find_offset_chunked, apply_offset_to_chart
                    print(f"\n  Auto-syncing: {ch_audio.name} → {ref_audio.name}")
                    sync_offset = find_offset_chunked(str(ref_audio), str(ch_audio))
                    print(f"  Sync offset: {sync_offset:+.4f}s")

                    # Apply offset to all drum timings
                    drums_timed = apply_offset_to_chart({"drums": drums_timed}, sync_offset)["drums"]
                    print(f"  Applied offset to all drum charts")
                except ImportError:
                    print("  Warning: numpy not available, skipping auto-sync")
                except Exception as e:
                    print(f"  Warning: Auto-sync failed ({e}), using raw chart timing")
            else:
                print("  Note: No audio pair found for auto-sync, using raw chart timing")

            # Save parsed drum data
            drums_dest = output_path / "drums"
            drums_dest.mkdir(exist_ok=True)

            (drums_dest / "chart_data.json").write_text(json.dumps({
                "metadata": {**chart_data["metadata"], **ini_meta},
                "sync_track": chart_data["sync_track"],
                "drums": drums_timed,
                "sync_offset_applied": sync_offset,
            }, indent=2))

            # Copy original chart for reference
            shutil.copy2(chart_file, drums_dest / chart_file.name)
            if ini_path.exists():
                shutil.copy2(ini_path, drums_dest / "song.ini")

            manifest["sources"]["clonehero"] = str(ch_path.name)
        elif chart_file and chart_file.suffix == ".mid":
            try:
                from midi_parser import parse_midi_drums, convert_to_timed

                print(f"  Parsing MIDI chart: {chart_file.name}")
                midi_chart = parse_midi_drums(chart_file)
                ini_meta = {}
                ini_path = ch_path / "song.ini"
                if ini_path.exists():
                    ini_meta = parse_song_ini(ini_path)

                # Convert drum notes to timed format
                drums_timed = {}
                for diff in ["expert", "hard", "medium", "easy"]:
                    notes = convert_to_timed(midi_chart, diff)
                    if notes:
                        drums_timed[diff] = notes
                        print(f"  Drums ({diff}): {len(notes)} notes")

                # Auto-sync (same logic as .chart path above)
                sync_offset = 0.0
                ch_audio = None
                ref_audio = None

                for ext in [".ogg", ".mp3", ".wav", ".opus"]:
                    candidate = ch_path / f"song{ext}"
                    if candidate.exists():
                        ch_audio = candidate
                        break

                if stems_path:
                    for ext in [".mp3", ".ogg", ".wav"]:
                        candidate = stems_path / f"original{ext}"
                        if candidate.exists():
                            ref_audio = candidate
                            break
                    if not ref_audio:
                        for ext in [".mp3", ".ogg", ".wav"]:
                            candidate = stems_path / f"guitar{ext}"
                            if candidate.exists():
                                ref_audio = candidate
                                break

                if ch_audio and ref_audio:
                    try:
                        from audio_sync import find_offset_chunked, apply_offset_to_chart
                        print(f"\n  Auto-syncing: {ch_audio.name} -> {ref_audio.name}")
                        sync_offset = find_offset_chunked(str(ref_audio), str(ch_audio))
                        print(f"  Sync offset: {sync_offset:+.4f}s")
                        drums_timed = apply_offset_to_chart({"drums": drums_timed}, sync_offset)["drums"]
                        print(f"  Applied offset to all drum charts")
                    except ImportError:
                        print("  Warning: numpy not available, skipping auto-sync")
                    except Exception as e:
                        print(f"  Warning: Auto-sync failed ({e}), using raw chart timing")
                else:
                    print("  Note: No audio pair found for auto-sync, using raw chart timing")

                # Save parsed drum data
                drums_dest = output_path / "drums"
                drums_dest.mkdir(exist_ok=True)

                (drums_dest / "chart_data.json").write_text(json.dumps({
                    "metadata": {**midi_chart["metadata"], **ini_meta},
                    "sync_track": midi_chart["sync_track"],
                    "drums": drums_timed,
                    "sync_offset_applied": sync_offset,
                }, indent=2))

                shutil.copy2(chart_file, drums_dest / chart_file.name)
                if ini_path.exists():
                    shutil.copy2(ini_path, drums_dest / "song.ini")

                manifest["sources"]["clonehero"] = str(ch_path.name)

            except ImportError:
                print(f"  Warning: mido not installed, copying .mid as-is (pip install mido)")
                drums_dest = output_path / "drums"
                drums_dest.mkdir(exist_ok=True)
                shutil.copy2(chart_file, drums_dest / chart_file.name)
                manifest["sources"]["clonehero"] = str(ch_path.name)
        else:
            print(f"  Warning: No chart file found in {ch_path}")

    # 3. Copy/link stems
    if stems_path and stems_path.exists():
        stems_dest = output_path / "stems"
        stems_dest.mkdir(exist_ok=True)
        stem_names = ["drums", "bass", "vocals", "guitar", "other"]
        copied = 0
        for name in stem_names:
            for ext in [".mp3", ".ogg", ".wav", ".flac"]:
                src = stems_path / f"{name}{ext}"
                if src.exists():
                    shutil.copy2(src, stems_dest / f"{name}{ext}")
                    copied += 1
                    break
        manifest["sources"]["stems"] = True
        print(f"  Stems: {copied} files copied")

    # 4. Parse and store vocals (from separate YARC/CH folder or from the drum chart folder)
    vox_source = vocals_path or ch_path  # Fall back to CH folder if no separate vocals
    if vox_source and vox_source.exists():
        chart_file = None
        for name in ["notes.chart", "Notes.chart"]:
            candidate = vox_source / name
            if candidate.exists():
                chart_file = candidate
                break

        if chart_file:
            # Check if chart has vocal sections
            chart_text = chart_file.read_text(encoding="utf-8", errors="replace")
            if "Vocals" in chart_text:
                # Parse sync track for timing
                chart_data = parse_chart_file(chart_file)
                resolution = int(chart_data["metadata"].get("Resolution", 192))

                vocals = parse_vocals_from_chart(
                    chart_file,
                    sync_track=chart_data["sync_track"],
                    resolution=resolution,
                )

                if vocals["lyrics"] or vocals["pitch_events"]:
                    # Auto-sync vocals to reference audio
                    vox_sync_offset = 0.0
                    vox_audio = None
                    ref_audio = None

                    for ext in [".ogg", ".mp3", ".wav", ".opus"]:
                        candidate = vox_source / f"song{ext}"
                        if candidate.exists():
                            vox_audio = candidate
                            break

                    if stems_path:
                        for ext in [".mp3", ".ogg", ".wav"]:
                            for fname in ["original", "vocals", "guitar"]:
                                candidate = stems_path / f"{fname}{ext}"
                                if candidate.exists():
                                    ref_audio = candidate
                                    break
                            if ref_audio:
                                break

                    if vox_audio and ref_audio and vox_source != ch_path:
                        # Only sync if vocals come from a different source than drums
                        try:
                            from audio_sync import find_offset_chunked
                            print(f"\n  Auto-syncing vocals: {vox_audio.name} -> {ref_audio.name}")
                            vox_sync_offset = find_offset_chunked(str(ref_audio), str(vox_audio))
                            print(f"  Vocal sync offset: {vox_sync_offset:+.4f}s")

                            # Apply offset to lyrics and pitch events
                            for item in vocals["lyrics"]:
                                item["time"] = round(max(0, item["time"] - vox_sync_offset), 4)
                            for item in vocals["pitch_events"]:
                                item["time"] = round(max(0, item["time"] - vox_sync_offset), 4)
                            print(f"  Applied vocal offset")
                        except Exception as e:
                            print(f"  Warning: Vocal auto-sync failed ({e})")
                    elif vox_source == ch_path:
                        # Vocals from same chart as drums — use same sync offset if available
                        print("  Vocals from same chart as drums — shared sync")

                    # Save vocal data
                    vocals_dest = output_path / "vocals"
                    vocals_dest.mkdir(exist_ok=True)

                    ini_meta = {}
                    ini_path = vox_source / "song.ini"
                    if ini_path.exists():
                        ini_meta = parse_song_ini(ini_path)

                    (vocals_dest / "vocal_data.json").write_text(json.dumps({
                        "metadata": {**chart_data["metadata"], **ini_meta},
                        "lyrics": vocals["lyrics"],
                        "pitch_events": vocals["pitch_events"],
                        "sync_offset_applied": vox_sync_offset,
                    }, indent=2))

                    shutil.copy2(chart_file, vocals_dest / chart_file.name)
                    manifest["sources"]["vocals"] = str(vox_source.name)
                    print(f"  Vocals: {len(vocals['lyrics'])} lyrics, {len(vocals['pitch_events'])} pitch events")

    # Save manifest
    (output_path / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nCombined package saved to: {output_path}")
    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Combine CDLC + Clone Hero/YARC + stems into unified package"
    )
    parser.add_argument("--cdlc", "-c", type=Path, help="CDLC .psarc file or folder (guitar/bass)")
    parser.add_argument("--clonehero", "-ch", type=Path, help="Clone Hero song folder for drums (with notes.chart)")
    parser.add_argument("--vocals", "-v", type=Path, help="Separate vocals chart folder (YARC/CH with [ExpertVocals])")
    parser.add_argument("--yarc", "-y", type=Path, help="YARC song folder (alias for --clonehero)")
    parser.add_argument("--stems", "-s", type=Path, help="Demucs stems folder")
    parser.add_argument("--output", "-o", required=True, type=Path, help="Output combined package folder")
    parser.add_argument("--song-name", "-n", help="Song display name (auto-detected if not given)")
    args = parser.parse_args()

    # YARC uses same format as Clone Hero
    ch_path = args.clonehero or args.yarc

    if not any([args.cdlc, ch_path, args.vocals, args.stems]):
        print("Error: Provide at least one source (--cdlc, --clonehero/--yarc, --vocals, --stems)")
        return 1

    print(f"Combining:")
    if args.cdlc:
        print(f"  Guitar/Bass: {args.cdlc}")
    if ch_path:
        print(f"  Drums: {ch_path}")
    if args.vocals:
        print(f"  Vocals: {args.vocals}")
    if args.stems:
        print(f"  Stems: {args.stems}")

    combine(args.cdlc, ch_path, args.stems, args.output, vocals_path=args.vocals)
    return 0


if __name__ == "__main__":
    exit(main())
