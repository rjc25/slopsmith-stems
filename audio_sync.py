#!/usr/bin/env python3
"""Audio synchronization via cross-correlation of spectrograms.

Finds the time offset between two audio files of the same song
(e.g., Clone Hero audio vs CDLC/Demucs audio) so that charts
from one source can be perfectly aligned with audio from the other.

Usage:
    from audio_sync import find_offset
    offset_seconds = find_offset("clonehero/song.ogg", "cdlc/original.mp3")
    # offset_seconds > 0 means CH audio starts LATER than CDLC audio
    # offset_seconds < 0 means CH audio starts EARLIER

Then shift all CH drum chart timings by subtracting offset_seconds.
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

import numpy as np


def load_audio_mono(filepath: str, sr: int = 22050, duration: float = None) -> np.ndarray:
    """Load audio file as mono numpy array using ffmpeg.

    Avoids requiring librosa for loading — just needs ffmpeg + numpy.
    """
    cmd = [
        "ffmpeg", "-i", str(filepath),
        "-ac", "1",           # mono
        "-ar", str(sr),       # sample rate
        "-f", "f32le",        # raw 32-bit float
        "-acodec", "pcm_f32le",
    ]
    if duration:
        cmd.extend(["-t", str(duration)])
    cmd.append("pipe:1")

    result = subprocess.run(cmd, capture_output=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()[:200]}")

    audio = np.frombuffer(result.stdout, dtype=np.float32)
    return audio


def compute_spectrogram(audio: np.ndarray, sr: int = 22050,
                        n_fft: int = 2048, hop_length: int = 512) -> np.ndarray:
    """Compute magnitude spectrogram using numpy STFT."""
    # Pad audio to fit complete frames
    n_frames = 1 + (len(audio) - n_fft) // hop_length
    if n_frames <= 0:
        raise ValueError("Audio too short for spectrogram")

    # Window
    window = np.hanning(n_fft)

    # STFT
    frames = np.zeros((n_fft, n_frames))
    for i in range(n_frames):
        start = i * hop_length
        frames[:, i] = audio[start:start + n_fft] * window

    # FFT
    spec = np.abs(np.fft.rfft(frames, axis=0))

    # Convert to log scale (like mel spectrogram but simpler)
    spec = np.log1p(spec)

    return spec


def cross_correlate_spectrograms(spec_a: np.ndarray, spec_b: np.ndarray) -> np.ndarray:
    """Cross-correlate two spectrograms along the time axis.

    Uses frequency-summed energy profiles for speed.
    Returns correlation array where the peak index gives the offset.
    """
    # Sum across frequency bands to get energy profile per time frame
    energy_a = np.sum(spec_a, axis=0)
    energy_b = np.sum(spec_b, axis=0)

    # Normalize
    energy_a = (energy_a - np.mean(energy_a)) / (np.std(energy_a) + 1e-8)
    energy_b = (energy_b - np.mean(energy_b)) / (np.std(energy_b) + 1e-8)

    # Cross-correlate
    correlation = np.correlate(energy_a, energy_b, mode="full")

    return correlation


def find_offset(audio_path_a: str, audio_path_b: str,
                sr: int = 22050, duration: float = 30.0,
                hop_length: int = 512) -> float:
    """Find the time offset between two audio files of the same song.

    Args:
        audio_path_a: Reference audio (e.g., CDLC/Demucs full mix)
        audio_path_b: Audio to align (e.g., Clone Hero song.ogg)
        sr: Sample rate for analysis
        duration: How many seconds to analyze (more = slower but more accurate)
        hop_length: STFT hop length

    Returns:
        Offset in seconds. Positive means audio_b starts AFTER audio_a.
        To sync charts from source B to audio A: subtract this offset from
        all chart timestamps.
    """
    print(f"Loading audio A: {audio_path_a}")
    audio_a = load_audio_mono(audio_path_a, sr=sr, duration=duration)

    print(f"Loading audio B: {audio_path_b}")
    audio_b = load_audio_mono(audio_path_b, sr=sr, duration=duration)

    print("Computing spectrograms...")
    spec_a = compute_spectrogram(audio_a, sr=sr, hop_length=hop_length)
    spec_b = compute_spectrogram(audio_b, sr=sr, hop_length=hop_length)

    print("Cross-correlating...")
    correlation = cross_correlate_spectrograms(spec_a, spec_b)

    # Find peak
    peak_index = np.argmax(correlation)
    center = len(spec_a[0]) - 1  # Center of correlation array

    # Convert frame offset to seconds
    frame_offset = peak_index - center
    offset_seconds = frame_offset * hop_length / sr

    # Confidence: how sharp is the peak vs noise
    peak_value = correlation[peak_index]
    noise_floor = np.median(np.abs(correlation))
    confidence = peak_value / (noise_floor + 1e-8)

    print(f"Offset: {offset_seconds:+.4f} seconds ({frame_offset:+d} frames)")
    print(f"Confidence: {confidence:.1f}x above noise floor")

    if confidence < 3.0:
        print("WARNING: Low confidence — audio files may not be the same song")

    return offset_seconds


def apply_offset_to_chart(chart_data: dict, offset_seconds: float) -> dict:
    """Apply a time offset to all drum notes in a parsed chart.

    Args:
        chart_data: Parsed chart data with drums[difficulty] arrays
        offset_seconds: Offset to subtract from all timestamps

    Returns:
        Modified chart_data with adjusted timestamps
    """
    for difficulty in chart_data.get("drums", {}):
        notes = chart_data["drums"][difficulty]
        for note in notes:
            if "time" in note:
                note["time"] = round(note["time"] - offset_seconds, 4)
                # Clamp to 0 (no negative timestamps)
                if note["time"] < 0:
                    note["time"] = 0

    return chart_data


def find_offset_chunked(audio_path_a: str, audio_path_b: str,
                        sr: int = 22050, chunk_duration: float = 10.0,
                        num_chunks: int = 3, hop_length: int = 512) -> float:
    """More robust offset finding using multiple chunks from different parts of the song.

    Computes offset from several segments and takes the median for robustness
    against intro differences, fade-ins, etc.
    """
    # Get total duration of shorter file
    audio_a_full = load_audio_mono(audio_path_a, sr=sr, duration=120)
    audio_b_full = load_audio_mono(audio_path_b, sr=sr, duration=120)

    total_duration = min(len(audio_a_full), len(audio_b_full)) / sr
    if total_duration < chunk_duration * 2:
        # Short song, just use the full thing
        return find_offset(audio_path_a, audio_path_b, sr=sr,
                          duration=total_duration, hop_length=hop_length)

    offsets = []
    # Sample chunks from beginning, middle, and later in the song
    # Skip first 5 seconds to avoid intro silence differences
    chunk_starts = np.linspace(5, total_duration - chunk_duration - 5, num_chunks)

    for start in chunk_starts:
        # Extract chunks
        start_sample = int(start * sr)
        end_sample = start_sample + int(chunk_duration * sr)

        chunk_a = audio_a_full[start_sample:end_sample]
        chunk_b = audio_b_full[start_sample:end_sample]

        if len(chunk_a) < sr or len(chunk_b) < sr:
            continue

        spec_a = compute_spectrogram(chunk_a, sr=sr, hop_length=hop_length)
        spec_b = compute_spectrogram(chunk_b, sr=sr, hop_length=hop_length)

        correlation = cross_correlate_spectrograms(spec_a, spec_b)
        peak_index = np.argmax(correlation)
        center = len(spec_a[0]) - 1

        frame_offset = peak_index - center
        offset_sec = frame_offset * hop_length / sr
        offsets.append(offset_sec)

    if not offsets:
        raise RuntimeError("Could not compute offset from any chunk")

    # Median is robust against outlier chunks (e.g., silence segments)
    median_offset = float(np.median(offsets))
    spread = float(np.std(offsets))

    print(f"Chunked offset analysis ({num_chunks} chunks):")
    print(f"  Individual offsets: {[f'{o:+.4f}s' for o in offsets]}")
    print(f"  Median offset: {median_offset:+.4f}s")
    print(f"  Spread (std): {spread:.4f}s")

    if spread > 0.1:
        print("WARNING: High spread between chunks — check that both files are the same song")

    return median_offset


def main():
    parser = argparse.ArgumentParser(
        description="Find time offset between two audio files of the same song"
    )
    parser.add_argument("audio_a", help="Reference audio (CDLC/Demucs)")
    parser.add_argument("audio_b", help="Audio to align (Clone Hero)")
    parser.add_argument("--duration", "-d", type=float, default=30,
                        help="Analysis duration in seconds (default: 30)")
    parser.add_argument("--chunked", "-c", action="store_true",
                        help="Use multi-chunk analysis for robustness")
    parser.add_argument("--apply-to", "-a", type=Path,
                        help="Apply offset to a chart_data.json file")
    args = parser.parse_args()

    if args.chunked:
        offset = find_offset_chunked(args.audio_a, args.audio_b)
    else:
        offset = find_offset(args.audio_a, args.audio_b, duration=args.duration)

    print(f"\nResult: {offset:+.4f} seconds")
    print(f"To sync Clone Hero charts to CDLC audio:")
    print(f"  Subtract {offset:.4f}s from all CH chart timestamps")

    if args.apply_to:
        import json
        chart_data = json.loads(args.apply_to.read_text())
        chart_data = apply_offset_to_chart(chart_data, offset)
        args.apply_to.write_text(json.dumps(chart_data, indent=2))
        print(f"\nApplied offset to {args.apply_to}")

    return 0


if __name__ == "__main__":
    exit(main())
