#!/usr/bin/env python3
"""Parse standard MIDI files (.mid) for Clone Hero drum tracks.

Handles Clone Hero's MIDI drum mapping where Expert drums use notes 96-100
and pro drum tom markers use notes 110-112. Outputs the same format as
the .chart parser in combine_charts.py so either parser can feed the
drum highway renderer.

Usage:
    from midi_parser import parse_midi_drums
    chart_data = parse_midi_drums(Path("notes.mid"))
    # Returns same structure as parse_chart_file() in combine_charts.py

Clone Hero MIDI Drum Mapping (Expert):
    96 = Kick
    97 = Red (Snare)
    98 = Yellow (Hi-Hat / Yellow Cymbal)
    99 = Blue (Tom / Blue Cymbal)
   100 = Green (Floor Tom / Green Cymbal)

Pro Drums Tom Markers (override cymbal to tom):
   110 = Yellow Tom marker (when active, note 98 = tom instead of cymbal)
   111 = Blue Tom marker (when active, note 99 = tom instead of cymbal)
   112 = Green Tom marker (when active, note 100 = tom instead of cymbal)

Difficulty offsets (each difficulty uses a 12-note range):
    Expert: 96-100
    Hard:   84-88
    Medium: 72-76
    Easy:   60-64
"""

import argparse
import json
from pathlib import Path

import mido


# ── Clone Hero MIDI Note Mappings ──

# Base note for each difficulty (kick note number)
DIFFICULTY_BASE = {
    "expert": 96,
    "hard": 84,
    "medium": 72,
    "easy": 60,
}

# Offset from base note to lane (same across all difficulties)
# 0=kick, 1=red/snare, 2=yellow, 3=blue, 4=green (5-lane uses +4=orange)
NOTE_OFFSET_TO_LANE = {
    0: {"name": "kick",   "color": "#FF8800", "lane": 0, "ch_note": 0},
    1: {"name": "red",    "color": "#FF0000", "lane": 1, "ch_note": 1},
    2: {"name": "yellow", "color": "#FFFF00", "lane": 2, "ch_note": 2},
    3: {"name": "blue",   "color": "#0088FF", "lane": 3, "ch_note": 3},
    4: {"name": "green",  "color": "#00FF00", "lane": 5, "ch_note": 5},
}

# Pro drums tom markers — these override cymbal→tom for the corresponding lane
# When a tom marker note is active at the same tick as a lane note,
# it means that note is a tom hit, not a cymbal hit.
TOM_MARKERS = {
    110: 2,  # Yellow: note 98/86/74/62 becomes tom instead of cymbal
    111: 3,  # Blue:   note 99/87/75/63 becomes tom instead of cymbal
    112: 4,  # Green:  note 100/88/76/64 becomes tom instead of cymbal
}

# Pro drum lane names when tom marker is active vs not
PRO_DRUM_NAMES = {
    2: {"cymbal": "Hi-Hat",     "tom": "Yellow Tom"},
    3: {"cymbal": "Blue Cymbal", "tom": "Blue Tom"},
    4: {"cymbal": "Green Cymbal","tom": "Floor Tom"},
}


def parse_midi_drums(midi_path: Path) -> dict:
    """Parse a Clone Hero MIDI file and extract drum track data.

    Returns the same structure as parse_chart_file() in combine_charts.py:
    {
        "metadata": { "Name": ..., "Resolution": ..., ... },
        "sync_track": [ { "tick": int, "bpm": float }, ... ],
        "drums": {
            "expert": [ { "tick", "note", "duration", "name", "color", "lane" }, ... ],
            "hard": [ ... ],
            "medium": [ ... ],
            "easy": [ ... ],
        }
    }
    """
    mid = mido.MidiFile(str(midi_path))

    result = {
        "metadata": {
            "Resolution": mid.ticks_per_beat,
            "Name": midi_path.stem,
        },
        "sync_track": [],
        "drums": {"expert": [], "hard": [], "medium": [], "easy": []},
    }

    # ── Extract tempo map from the first track (or any track with tempo events) ──
    _extract_tempo_map(mid, result)

    # ── Find the drum track ──
    drum_track = _find_drum_track(mid)
    if drum_track is None:
        return result

    # ── Parse drum notes ──
    # First pass: collect all note_on events and tom markers by tick
    raw_notes = {}   # { difficulty: [ { tick, offset, duration } ] }
    tom_marker_ticks = {}  # { marker_offset: set(ticks_where_active) }
    active_notes = {}  # { (difficulty, offset): start_tick } for tracking duration

    for diff in DIFFICULTY_BASE:
        raw_notes[diff] = []

    # Track tom marker state
    active_tom_markers = {}  # { midi_note: start_tick }

    abs_tick = 0
    for msg in drum_track:
        abs_tick += msg.time

        if msg.type == "note_on" and msg.velocity > 0:
            # Check if this is a tom marker
            if msg.note in TOM_MARKERS:
                active_tom_markers[msg.note] = abs_tick
                continue

            # Check if this note belongs to any difficulty
            for diff, base in DIFFICULTY_BASE.items():
                offset = msg.note - base
                if offset in NOTE_OFFSET_TO_LANE:
                    active_notes[(diff, offset)] = abs_tick
                    break

        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            # End of tom marker
            if msg.note in TOM_MARKERS:
                marker_offset = TOM_MARKERS[msg.note]
                start = active_tom_markers.pop(msg.note, None)
                if start is not None:
                    if marker_offset not in tom_marker_ticks:
                        tom_marker_ticks[marker_offset] = set()
                    # Mark all ticks in the range as having the tom marker active
                    tom_marker_ticks[marker_offset].add((start, abs_tick))
                continue

            # End of a drum note
            for diff, base in DIFFICULTY_BASE.items():
                offset = msg.note - base
                if offset in NOTE_OFFSET_TO_LANE:
                    start_tick = active_notes.pop((diff, offset), None)
                    if start_tick is not None:
                        duration = abs_tick - start_tick
                        raw_notes[diff].append({
                            "tick": start_tick,
                            "offset": offset,
                            "duration": max(duration, 1),
                        })
                    break

    # ── Convert raw notes to final format with pro drum info ──
    for diff in raw_notes:
        for raw in sorted(raw_notes[diff], key=lambda n: n["tick"]):
            lane_info = NOTE_OFFSET_TO_LANE[raw["offset"]]
            note_entry = {
                "tick": raw["tick"],
                "note": raw["offset"],  # CH-style note number (0-5)
                "duration": raw["duration"],
                "name": lane_info["name"],
                "color": lane_info["color"],
                "lane": lane_info["lane"],
            }

            # Apply pro drum tom marker logic
            offset = raw["offset"]
            if offset in PRO_DRUM_NAMES:
                is_tom = _is_tom_marker_active(
                    raw["tick"], offset, tom_marker_ticks
                )
                if is_tom:
                    note_entry["pro_type"] = "tom"
                else:
                    note_entry["pro_type"] = "cymbal"

            result["drums"][diff].append(note_entry)

    return result


def _extract_tempo_map(mid: mido.MidiFile, result: dict):
    """Extract BPM changes and time signatures from the MIDI file."""
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time

            if msg.type == "set_tempo":
                bpm = mido.tempo2bpm(msg.tempo)
                result["sync_track"].append({
                    "tick": abs_tick,
                    "bpm": round(bpm, 3),
                })

            elif msg.type == "time_signature":
                result["sync_track"].append({
                    "tick": abs_tick,
                    "ts_numerator": msg.numerator,
                    "ts_denominator": msg.denominator,
                })

            elif msg.type == "track_name":
                # Capture song name from MIDI metadata
                if msg.name and not result["metadata"].get("Name_from_midi"):
                    result["metadata"]["Name_from_midi"] = msg.name


def _find_drum_track(mid: mido.MidiFile):
    """Find the MIDI track containing drum note data.

    Clone Hero MIDI files typically name the drum track "PART DRUMS".
    Falls back to scanning all tracks for drum-range notes.
    """
    # First: look for a track named "PART DRUMS"
    for track in mid.tracks:
        for msg in track:
            if msg.type == "track_name" and "drum" in msg.name.lower():
                return track

    # Fallback: find a track with notes in the drum range (60-112)
    for track in mid.tracks:
        for msg in track:
            if msg.type == "note_on" and 60 <= msg.note <= 112:
                return track

    return None


def _is_tom_marker_active(tick: int, offset: int, tom_marker_ticks: dict) -> bool:
    """Check if a tom marker is active at the given tick for the given lane offset."""
    ranges = tom_marker_ticks.get(offset, set())
    for start, end in ranges:
        if start <= tick <= end:
            return True
    return False


def ticks_to_seconds(tick: int, sync_track: list, resolution: int = 192) -> float:
    """Convert chart ticks to seconds using BPM data.

    Same algorithm as combine_charts.py for compatibility.
    """
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
        delta_ticks = event["tick"] - last_tick
        seconds += (delta_ticks / resolution) * (60.0 / last_bpm)
        last_tick = event["tick"]
        last_bpm = event["bpm"]

    delta_ticks = tick - last_tick
    seconds += (delta_ticks / resolution) * (60.0 / last_bpm)

    return seconds


def convert_to_timed(chart_data: dict, difficulty: str = "expert") -> list:
    """Convert tick-based drum notes to time-based (seconds).

    Same output format as convert_drums_to_timed() in combine_charts.py.
    """
    resolution = int(chart_data["metadata"].get("Resolution", 192))
    sync_track = chart_data["sync_track"]
    drum_notes = chart_data["drums"].get(difficulty, [])

    timed_notes = []
    for note in drum_notes:
        time_sec = ticks_to_seconds(note["tick"], sync_track, resolution)
        entry = {
            "time": round(time_sec, 4),
            "note": note["note"],
            "name": note["name"],
            "color": note["color"],
            "lane": note["lane"],
            "duration": note["duration"],
        }
        if "pro_type" in note:
            entry["pro_type"] = note["pro_type"]
        timed_notes.append(entry)

    return sorted(timed_notes, key=lambda n: n["time"])


def main():
    parser = argparse.ArgumentParser(
        description="Parse Clone Hero MIDI drum charts"
    )
    parser.add_argument("midi_file", type=Path, help="Path to .mid file")
    parser.add_argument("--output", "-o", type=Path, help="Output JSON file (default: stdout)")
    parser.add_argument("--difficulty", "-d", default="expert",
                        choices=["expert", "hard", "medium", "easy"],
                        help="Difficulty to convert to timed format")
    parser.add_argument("--timed", "-t", action="store_true",
                        help="Output time-based (seconds) instead of tick-based")
    args = parser.parse_args()

    if not args.midi_file.exists():
        print(f"Error: {args.midi_file} not found")
        return 1

    chart_data = parse_midi_drums(args.midi_file)

    # Convert to timed format if requested
    if args.timed:
        for diff in ["expert", "hard", "medium", "easy"]:
            notes = convert_to_timed(chart_data, diff)
            if notes:
                chart_data["drums"][diff] = notes
                print(f"  {diff}: {len(notes)} notes converted to timed format")

    # Stats
    for diff in ["expert", "hard", "medium", "easy"]:
        count = len(chart_data["drums"].get(diff, []))
        if count:
            print(f"  {diff}: {count} drum notes")

    tempo_changes = len([e for e in chart_data["sync_track"] if "bpm" in e])
    print(f"  Tempo changes: {tempo_changes}")
    print(f"  Resolution: {chart_data['metadata'].get('Resolution', '?')} ticks/beat")

    output = json.dumps(chart_data, indent=2)

    if args.output:
        args.output.write_text(output)
        print(f"\nSaved to {args.output}")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    exit(main())
