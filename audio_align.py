#!/usr/bin/env python3
"""Per-note audio alignment using chroma features + Dynamic Time Warping.

The ideal sync pipeline:
1. GP tab → MIDI audio (FluidSynth renders chart timing as audio)
2. Real recording → Demucs → instrument stem (e.g., guitar)
3. Chroma features from both (pitch-class, timbre-independent)
4. DTW finds optimal frame-by-frame alignment path
5. Every chart note gets individually warped to the real audio's timing

This handles: tempo drift, rubato, live recordings, human timing
variations, different intros/outros, and per-note corrections.

Resolution: ~23ms per frame (hop_length=512 at sr=22050).

Requires: librosa, numpy
    pip install librosa numpy
"""

import argparse
import json
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np

# Try librosa, fall back to manual chroma if not available
try:
    import librosa
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False


def load_audio(filepath: str, sr: int = 22050, duration: float = None) -> np.ndarray:
    """Load audio as mono float32 array."""
    if HAS_LIBROSA:
        y, _ = librosa.load(filepath, sr=sr, mono=True, duration=duration)
        return y

    # Fallback: ffmpeg
    cmd = ["ffmpeg", "-i", str(filepath), "-ac", "1", "-ar", str(sr),
           "-f", "f32le", "-acodec", "pcm_f32le"]
    if duration:
        cmd.extend(["-t", str(duration)])
    cmd.append("pipe:1")
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()[:200]}")
    return np.frombuffer(result.stdout, dtype=np.float32)


def compute_chroma(audio: np.ndarray, sr: int = 22050,
                   hop_length: int = 512) -> np.ndarray:
    """Compute chroma features (12 pitch classes over time).

    Chroma is timbre-independent — a distorted guitar E chord and a
    FluidSynth piano E chord have the same chroma. This is what makes
    MIDI-to-real-audio comparison possible.
    """
    if HAS_LIBROSA:
        return librosa.feature.chroma_cqt(y=audio, sr=sr, hop_length=hop_length)

    # Fallback: basic STFT-based chroma (less accurate but works without librosa)
    n_fft = 4096
    n_frames = 1 + (len(audio) - n_fft) // hop_length
    if n_frames <= 0:
        raise ValueError("Audio too short")

    window = np.hanning(n_fft)
    chroma = np.zeros((12, n_frames))

    for i in range(n_frames):
        start = i * hop_length
        frame = audio[start:start + n_fft] * window
        spectrum = np.abs(np.fft.rfft(frame))
        freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)

        # Map frequency bins to pitch classes
        for bin_idx in range(1, len(freqs)):
            if freqs[bin_idx] > 20 and freqs[bin_idx] < 5000:
                midi_note = 12 * np.log2(freqs[bin_idx] / 440.0 + 1e-10) + 69
                pitch_class = int(round(midi_note)) % 12
                chroma[pitch_class, i] += spectrum[bin_idx]

    # Normalize per frame
    norms = np.maximum(np.linalg.norm(chroma, axis=0, keepdims=True), 1e-8)
    chroma = chroma / norms
    return chroma


def dtw_align(chroma_midi: np.ndarray, chroma_real: np.ndarray,
              ) -> list[tuple[int, int]]:
    """Dynamic Time Warping to find optimal frame alignment.

    Returns: list of (midi_frame, real_frame) tuples — the warp path.
    """
    if HAS_LIBROSA:
        D, wp = librosa.sequence.dtw(
            X=chroma_midi, Y=chroma_real,
            metric="cosine",
            backtrack=True,
        )
        # librosa returns path from end to start, reverse it
        wp = wp[::-1]
        return [(int(row[0]), int(row[1])) for row in wp]

    # Fallback: basic DTW implementation
    n = chroma_midi.shape[1]
    m = chroma_real.shape[1]

    # Cost matrix (cosine distance between chroma frames)
    cost = np.zeros((n, m))
    for i in range(n):
        for j in range(m):
            dot = np.dot(chroma_midi[:, i], chroma_real[:, j])
            norm_a = np.linalg.norm(chroma_midi[:, i])
            norm_b = np.linalg.norm(chroma_real[:, j])
            cost[i, j] = 1 - dot / (norm_a * norm_b + 1e-8)

    # Accumulated cost matrix
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i, j] = cost[i - 1, j - 1] + min(
                D[i - 1, j],      # insertion
                D[i, j - 1],      # deletion
                D[i - 1, j - 1],  # match
            )

    # Backtrack
    path = []
    i, j = n, m
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        choices = [
            (D[i - 1, j - 1], i - 1, j - 1),
            (D[i - 1, j], i - 1, j),
            (D[i, j - 1], i, j - 1),
        ]
        _, i, j = min(choices, key=lambda x: x[0])

    path.reverse()
    return path


def build_warp_function(warp_path: list[tuple[int, int]],
                        sr: int = 22050, hop_length: int = 512) -> dict:
    """Convert DTW warp path to a time-based lookup function.

    Returns a dict with:
    - warp_table: list of (midi_time, real_time) pairs at frame resolution
    - midi_to_real: callable that converts midi_time → real_time
    """
    warp_table = []
    for midi_frame, real_frame in warp_path:
        midi_time = midi_frame * hop_length / sr
        real_time = real_frame * hop_length / sr
        warp_table.append((round(midi_time, 4), round(real_time, 4)))

    def midi_to_real(t: float) -> float:
        """Convert a chart time (MIDI-based) to real audio time."""
        if not warp_table:
            return t

        # Binary search for surrounding warp points
        lo, hi = 0, len(warp_table) - 1

        if t <= warp_table[0][0]:
            # Before first point: extrapolate from first offset
            offset = warp_table[0][1] - warp_table[0][0]
            return max(0, t + offset)

        if t >= warp_table[-1][0]:
            # After last point: extrapolate from last offset
            offset = warp_table[-1][1] - warp_table[-1][0]
            return t + offset

        # Find surrounding points
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if warp_table[mid][0] <= t:
                lo = mid
            else:
                hi = mid

        # Linear interpolation between warp points
        t0, r0 = warp_table[lo]
        t1, r1 = warp_table[hi]

        if t1 == t0:
            return r0

        frac = (t - t0) / (t1 - t0)
        return r0 + frac * (r1 - r0)

    return {
        "warp_table": warp_table,
        "midi_to_real": midi_to_real,
        "total_midi_frames": warp_path[-1][0] if warp_path else 0,
        "total_real_frames": warp_path[-1][1] if warp_path else 0,
    }


def align_chart_notes(notes: list[dict], warp_fn: callable) -> list[dict]:
    """Apply the warp function to every note in a chart.

    Each note dict must have a 'time' field (seconds).
    Returns the same notes with corrected 'time' values.
    Also adds 'original_time' for reference.
    """
    aligned = []
    for note in notes:
        corrected = dict(note)
        corrected["original_time"] = note["time"]
        corrected["time"] = round(warp_fn(note["time"]), 4)
        aligned.append(corrected)
    return aligned


def full_alignment_pipeline(
    midi_audio_path: str,
    real_audio_path: str,
    chart_notes: list[dict],
    sr: int = 22050,
    hop_length: int = 512,
    on_progress: Optional[callable] = None,
) -> dict:
    """Run the complete alignment pipeline.

    Args:
        midi_audio_path: MIDI-rendered audio (from GP/chart, timing reference)
        real_audio_path: Real recording (or Demucs stem for cleaner match)
        chart_notes: List of note dicts with 'time' field
        sr: Sample rate for analysis
        hop_length: STFT hop length (~23ms per frame at 22050Hz)
        on_progress: Optional callback(message, percent)

    Returns: {
        "aligned_notes": [...],     # notes with corrected times
        "warp_table": [...],        # (midi_time, real_time) pairs
        "stats": {
            "total_notes": int,
            "avg_shift": float,     # average time shift in seconds
            "max_shift": float,     # maximum time shift
            "drift_detected": bool, # True if shift varies significantly
        }
    }
    """
    def progress(msg, pct):
        if on_progress:
            on_progress(msg, pct)
        print(f"  [{pct}%] {msg}")

    progress("Loading MIDI reference audio...", 10)
    midi_audio = load_audio(midi_audio_path, sr=sr)

    progress("Loading real audio...", 20)
    real_audio = load_audio(real_audio_path, sr=sr)

    progress("Computing chroma features (MIDI)...", 30)
    chroma_midi = compute_chroma(midi_audio, sr=sr, hop_length=hop_length)

    progress("Computing chroma features (real audio)...", 40)
    chroma_real = compute_chroma(real_audio, sr=sr, hop_length=hop_length)

    progress(f"Running DTW alignment ({chroma_midi.shape[1]} × {chroma_real.shape[1]} frames)...", 50)
    warp_path = dtw_align(chroma_midi, chroma_real)

    progress("Building warp function...", 70)
    warp_data = build_warp_function(warp_path, sr=sr, hop_length=hop_length)

    progress("Aligning chart notes...", 80)
    aligned_notes = align_chart_notes(chart_notes, warp_data["midi_to_real"])

    # Compute stats
    shifts = [abs(n["time"] - n["original_time"]) for n in aligned_notes if "original_time" in n]
    avg_shift = float(np.mean(shifts)) if shifts else 0.0
    max_shift = float(np.max(shifts)) if shifts else 0.0

    # Check for drift (does the shift change over time?)
    if len(aligned_notes) > 10:
        first_quarter = shifts[:len(shifts) // 4]
        last_quarter = shifts[-(len(shifts) // 4):]
        drift = abs(np.mean(first_quarter) - np.mean(last_quarter))
        drift_detected = drift > 0.05  # More than 50ms drift across the song
    else:
        drift_detected = False

    progress(f"Done. Avg shift: {avg_shift:.3f}s, Max: {max_shift:.3f}s, Drift: {drift_detected}", 100)

    return {
        "aligned_notes": aligned_notes,
        "warp_table": warp_data["warp_table"],
        "stats": {
            "total_notes": len(aligned_notes),
            "avg_shift": round(avg_shift, 4),
            "max_shift": round(max_shift, 4),
            "drift_detected": drift_detected,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Align chart notes to real audio using chroma + DTW"
    )
    parser.add_argument("midi_audio", help="MIDI-rendered audio (timing reference)")
    parser.add_argument("real_audio", help="Real audio recording (or Demucs stem)")
    parser.add_argument("--chart", "-c", type=Path, help="chart_data.json with notes to align")
    parser.add_argument("--output", "-o", type=Path, help="Output aligned chart JSON")
    args = parser.parse_args()

    if args.chart:
        chart_data = json.loads(args.chart.read_text())
        # Flatten all difficulty notes
        all_notes = []
        for diff, notes in chart_data.get("drums", {}).items():
            all_notes.extend(notes)
        if not all_notes:
            all_notes = chart_data.get("notes", [])
    else:
        all_notes = []

    result = full_alignment_pipeline(args.midi_audio, args.real_audio, all_notes)

    print(f"\nAlignment stats:")
    print(f"  Notes: {result['stats']['total_notes']}")
    print(f"  Avg shift: {result['stats']['avg_shift']}s")
    print(f"  Max shift: {result['stats']['max_shift']}s")
    print(f"  Drift: {'YES' if result['stats']['drift_detected'] else 'no'}")

    if args.output:
        args.output.write_text(json.dumps(result, indent=2, default=str))
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
