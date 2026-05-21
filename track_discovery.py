#!/usr/bin/env python3
"""Track discovery for Clone Hero charts and Rocksmith CDLC.

Scans chart files and PSARC archives to find ALL available tracks,
presents them to the user for manual assignment.

Clone Hero .chart sections might include:
  [ExpertSingle], [ExpertDrums], [ExpertVocals], [HardDrums],
  [MediumSingle], [EasySingle], etc.
  Or non-standard: [ExpertGuitar], [ExpertKeys], [ExpertRealDrums], etc.

Rocksmith CDLC arrangements might include:
  Lead, Rhythm, Bass, Lead1, Lead2, Combo, Bonus, etc.
"""

import json
import re
from pathlib import Path
from typing import Optional


# ── Clone Hero Chart Discovery ──

def discover_chart_tracks(chart_path: Path) -> dict:
    """Scan a .chart file and return all available track sections with metadata.

    Returns: {
        "file": str,
        "metadata": { "Name": ..., "Artist": ..., "Resolution": ... },
        "sections": [
            {
                "name": "[ExpertDrums]",
                "raw_name": "ExpertDrums",
                "note_count": 234,
                "difficulty": "Expert",
                "instrument_guess": "drums",
                "has_lyrics": false,
                "has_notes": true,
                "sample_notes": [0, 1, 2, 3, 5]  # first few note values
            },
            ...
        ]
    }
    """
    text = chart_path.read_text(encoding="utf-8", errors="replace")

    result = {
        "file": str(chart_path),
        "metadata": {},
        "sections": [],
    }

    # Parse metadata
    in_song = False
    for line in text.split("\n"):
        line = line.strip()
        if line == "[Song]":
            in_song = True
            continue
        if line.startswith("[") and in_song:
            in_song = False
        if in_song:
            match = re.match(r'(\w+)\s*=\s*"?(.+?)"?\s*$', line)
            if match:
                result["metadata"][match.group(1)] = match.group(2)

    # Discover all sections
    current_section = None
    section_data = {}

    for line in text.split("\n"):
        line = line.strip()

        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            if current_section not in ("Song", "SyncTrack", "Events"):
                section_data[current_section] = {
                    "notes": [],
                    "events": [],
                    "note_values": set(),
                }
            continue

        if current_section and current_section in section_data:
            # Note events
            note_match = re.match(r'(\d+)\s*=\s*N\s+(\d+)\s+(\d+)', line)
            if note_match:
                section_data[current_section]["notes"].append({
                    "tick": int(note_match.group(1)),
                    "note": int(note_match.group(2)),
                    "duration": int(note_match.group(3)),
                })
                section_data[current_section]["note_values"].add(int(note_match.group(2)))

            # Lyric/text events
            event_match = re.match(r'(\d+)\s*=\s*E\s+"([^"]*)"', line)
            if event_match:
                section_data[current_section]["events"].append({
                    "tick": int(event_match.group(1)),
                    "text": event_match.group(2),
                })

    # Analyze each section
    for section_name, data in section_data.items():
        # Guess difficulty
        difficulty = "Unknown"
        for diff in ["Expert", "Hard", "Medium", "Easy"]:
            if diff.lower() in section_name.lower():
                difficulty = diff
                break

        # Guess instrument
        instrument_guess = guess_instrument(section_name, data)

        result["sections"].append({
            "name": f"[{section_name}]",
            "raw_name": section_name,
            "note_count": len(data["notes"]),
            "event_count": len(data["events"]),
            "difficulty": difficulty,
            "instrument_guess": instrument_guess,
            "has_lyrics": len(data["events"]) > 0,
            "has_notes": len(data["notes"]) > 0,
            "sample_notes": sorted(list(data["note_values"]))[:10],
            "sample_lyrics": [e["text"] for e in data["events"][:5]],
        })

    # Sort: Expert first, then by note count
    diff_order = {"Expert": 0, "Hard": 1, "Medium": 2, "Easy": 3, "Unknown": 4}
    result["sections"].sort(key=lambda s: (diff_order.get(s["difficulty"], 4), -s["note_count"]))

    return result


def guess_instrument(section_name: str, data: dict) -> str:
    """Guess what instrument a chart section contains based on name and note patterns."""
    name_lower = section_name.lower()

    # Explicit name matches
    if "drum" in name_lower:
        return "drums"
    if "vocal" in name_lower or "lyric" in name_lower or "sing" in name_lower:
        return "vocals"
    if "bass" in name_lower:
        return "bass"
    if "key" in name_lower or "piano" in name_lower:
        return "keys"
    if "guitar" in name_lower or "single" in name_lower or "doublebass" in name_lower:
        return "guitar"
    if "rhythm" in name_lower:
        return "rhythm_guitar"
    if "ghl" in name_lower:
        return "guitar"  # Guitar Hero Live 6-fret

    # Pattern-based guessing
    note_values = data.get("note_values", set())

    # Drums typically use notes 0-5 (kick, red, yellow, blue, orange, green)
    if note_values and max(note_values) <= 5 and 0 in note_values:
        return "drums"

    # Guitar typically uses notes 0-4 (green, red, yellow, blue, orange)
    # but without kick (0 in drums means kick, in guitar means green)
    if note_values and max(note_values) <= 7 and len(note_values) >= 3:
        if not data.get("events"):
            return "guitar"

    # Vocals have lyric events
    if len(data.get("events", [])) > 10:
        return "vocals"

    return "unknown"


# ── Rocksmith CDLC Arrangement Discovery ──

def discover_cdlc_arrangements(psarc_path: Path) -> dict:
    """Discover all arrangements in a Rocksmith CDLC .psarc file.

    Returns: {
        "file": str,
        "arrangements": [
            {
                "name": "Lead",
                "type": "guitar",  # guitar, bass, vocals, showlights
                "tuning": "E Standard",
                "sections": 12,
                "notes": 456,
                "path_inside_psarc": "songs/arr/song_lead.xml"
            },
            ...
        ]
    }
    """
    result = {
        "file": str(psarc_path),
        "arrangements": [],
    }

    # Try to extract file listing from PSARC
    try:
        import struct
        import zlib

        with open(psarc_path, "rb") as f:
            magic = f.read(4)
            if magic != b"PSAR":
                return result

            version = struct.unpack(">I", f.read(4))[0]
            compression = f.read(4)
            toc_length = struct.unpack(">I", f.read(4))[0]
            toc_entry_size = struct.unpack(">I", f.read(4))[0]
            toc_entry_count = struct.unpack(">I", f.read(4))[0]
            block_size = struct.unpack(">I", f.read(4))[0]
            if block_size == 0:
                block_size = 65536

            # Read entries
            entries = []
            for i in range(toc_entry_count):
                entry_data = f.read(toc_entry_size)
                if len(entry_data) < 30:
                    continue
                uncomp_size = int.from_bytes(entry_data[20:25], "big")
                file_offset = int.from_bytes(entry_data[25:30], "big")
                entries.append({"size": uncomp_size, "offset": file_offset})

            if not entries:
                return result

            # First entry is file listing
            f.seek(entries[0]["offset"])
            try:
                raw = f.read(min(entries[0]["size"] * 2, 65536))
                name_data = zlib.decompress(raw)
                names = name_data.decode("utf-8", errors="replace").split("\n")
            except Exception:
                names = []

            # Find arrangement files
            for name in names:
                name = name.strip()
                if not name:
                    continue

                # Rocksmith arrangements are typically named like:
                # songs/arr/songname_lead.xml, songname_rhythm.xml, songname_bass.xml
                name_lower = name.lower()

                arr_type = None
                arr_name = None

                if name_lower.endswith(".xml") and "/arr/" in name_lower:
                    basename = Path(name).stem.lower()

                    if "lead" in basename:
                        arr_type = "guitar"
                        if "lead2" in basename or "lead_2" in basename:
                            arr_name = "Lead 2"
                        elif "lead1" in basename or "lead_1" in basename:
                            arr_name = "Lead 1"
                        else:
                            arr_name = "Lead"
                    elif "rhythm" in basename:
                        arr_type = "guitar"
                        arr_name = "Rhythm"
                    elif "combo" in basename:
                        arr_type = "guitar"
                        arr_name = "Combo"
                    elif "bass" in basename:
                        arr_type = "bass"
                        arr_name = "Bass"
                    elif "vocal" in basename:
                        arr_type = "vocals"
                        arr_name = "Vocals"
                    elif "showlight" in basename:
                        arr_type = "showlights"
                        arr_name = "Show Lights"
                    elif "bonus" in basename:
                        arr_type = "guitar"
                        arr_name = "Bonus"

                    if arr_type:
                        result["arrangements"].append({
                            "name": arr_name,
                            "type": arr_type,
                            "path_inside_psarc": name,
                        })

                # Also track audio and other important files
                elif any(name_lower.endswith(ext) for ext in [".wem", ".ogg", ".bnk"]):
                    if "preview" not in name_lower:
                        result["audio_file"] = name

    except Exception as e:
        result["error"] = str(e)

    return result


def discover_all(chart_path: Optional[Path] = None,
                 cdlc_path: Optional[Path] = None) -> dict:
    """Discover tracks from both chart files and CDLC.

    Returns combined discovery with all available tracks for user assignment.
    """
    result = {
        "chart_tracks": None,
        "cdlc_arrangements": None,
        "suggested_mapping": {},
    }

    if chart_path and chart_path.exists():
        if chart_path.is_dir():
            # Look for chart files in directory
            for name in ["notes.chart", "Notes.chart", "notes.mid"]:
                candidate = chart_path / name
                if candidate.exists():
                    chart_path = candidate
                    break

        if chart_path.is_file() and chart_path.suffix == ".chart":
            result["chart_tracks"] = discover_chart_tracks(chart_path)

    if cdlc_path and cdlc_path.exists() and cdlc_path.suffix.lower() == ".psarc":
        result["cdlc_arrangements"] = discover_cdlc_arrangements(cdlc_path)

    # Build suggested mapping
    if result["chart_tracks"]:
        for section in result["chart_tracks"]["sections"]:
            if section["instrument_guess"] != "unknown" and section["difficulty"] == "Expert":
                instrument = section["instrument_guess"]
                if instrument not in result["suggested_mapping"]:
                    result["suggested_mapping"][instrument] = {
                        "source": "chart",
                        "section": section["raw_name"],
                        "note_count": section["note_count"],
                    }

    if result["cdlc_arrangements"]:
        for arr in result["cdlc_arrangements"]["arrangements"]:
            instrument = arr["type"]
            if instrument not in result["suggested_mapping"]:
                result["suggested_mapping"][instrument] = {
                    "source": "cdlc",
                    "arrangement": arr["name"],
                }

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Discover available tracks in chart files and CDLC")
    parser.add_argument("--chart", "-c", type=Path, help="Clone Hero .chart file or folder")
    parser.add_argument("--cdlc", "-p", type=Path, help="Rocksmith CDLC .psarc file")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = discover_all(args.chart, args.cdlc)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    # Pretty print
    if result["chart_tracks"]:
        ct = result["chart_tracks"]
        meta = ct["metadata"]
        print(f"\nChart: {ct['file']}")
        print(f"Song: {meta.get('Name', '?')} - {meta.get('Artist', '?')}")
        print(f"Resolution: {meta.get('Resolution', '?')}")
        print(f"\nTracks found ({len(ct['sections'])}):")
        for s in ct["sections"]:
            status = []
            if s["has_notes"]:
                status.append(f"{s['note_count']} notes")
            if s["has_lyrics"]:
                status.append(f"{s['event_count']} lyrics")
            guess = f" → {s['instrument_guess']}" if s["instrument_guess"] != "unknown" else ""
            print(f"  {s['name']:30s} {s['difficulty']:8s} {', '.join(status):20s}{guess}")
            if s["sample_lyrics"]:
                print(f"    Lyrics preview: {' '.join(s['sample_lyrics'][:8])}")

    if result["cdlc_arrangements"]:
        ca = result["cdlc_arrangements"]
        print(f"\nCDLC: {ca['file']}")
        print(f"Arrangements ({len(ca['arrangements'])}):")
        for arr in ca["arrangements"]:
            print(f"  {arr['name']:15s} ({arr['type']})")

    if result["suggested_mapping"]:
        print(f"\nSuggested mapping:")
        for instrument, source in result["suggested_mapping"].items():
            if source["source"] == "chart":
                print(f"  {instrument:15s} ← Chart [{source['section']}] ({source['note_count']} notes)")
            else:
                print(f"  {instrument:15s} ← CDLC [{source['arrangement']}]")


if __name__ == "__main__":
    main()
