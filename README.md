# SlopSmith Stems — Full Band Platform

Turn SlopSmith into a real-instrument Rock Band. Stem separation, drum charts from Clone Hero, vocal pitch detection, and multiplayer — all open source.

**Play any song with your real guitar, real drums, real voice. Toggle any instrument on/off mid-song. Play together as a full band.**

## The Vision

```
Player 1: Guitar  → SlopSmith CDLC tab highway + Axe-FX
Player 2: Drums   → Clone Hero drum lanes + MIDI kit
Player 3: Vocals  → Pitch highway + microphone
Player 4: Bass    → SlopSmith CDLC bass highway

Each player's instrument stem is auto-muted.
You hear the full band minus YOUR part. You fill the gap.
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your Replicate API key (for Demucs stem separation)
export REPLICATE_API_TOKEN=r8_your_token_here

# 3. Process your song library (~$0.021 per song)
python batch_process.py --library /path/to/cdlc/ --output /path/to/stems/ --dry-run
python batch_process.py --library /path/to/cdlc/ --output /path/to/stems/ --workers 5

# 4. Combine with Clone Hero drum charts
python combine_charts.py \
  --cdlc /path/to/song.psarc \
  --clonehero /path/to/ch/song_folder/ \
  --stems /path/to/stems/song/ \
  --output /path/to/combined/song/

# 5. Copy plugins into SlopSmith
cp -r plugins/* /path/to/slopsmith/plugins/

# 6. Launch SlopSmith and open the Dashboard
```

## Features

### Stem Separation Pipeline
- **Demucs AI** splits any song into 5 stems: drums, bass, vocals, guitar, other
- **Batch processing** with parallel workers, resume support, progress tracking
- **$0.021 per song** — process 1,000 songs for ~$21
- **Backing track generator** — creates `no_guitar.mp3`, `no_vocals.mp3`, `drums_only.mp3`, etc.

### Real-Time Stem Toggle
Toggle any instrument on/off while playing with keyboard shortcuts:

| Key | Action |
|-----|--------|
| G | Toggle guitar |
| V | Toggle vocals |
| B | Toggle bass |
| D | Toggle drums |
| O | Toggle other |
| A | All stems on |
| S | Solo guitar |

Smooth 150ms crossfade. No clicks or pops. All stems stay perfectly synced.

### Clone Hero Drum Highway
- **Exact Clone Hero colors**: orange kick, red snare, yellow hi-hat, blue tom, green floor tom
- **Scrolling note gems** with glow effects and gradient fills
- **MIDI drum kit input** via Web MIDI API — auto-detects Roland, Alesis, Yamaha, any GM kit
- **MIDI Learn** — custom pad mapping per kit from the settings page
- **Hit detection** with ±75ms tolerance (matches Clone Hero Expert)
- **Scoring**: accuracy %, streak counter, hit/miss visual feedback
- **Difficulty switching**: 1=Easy, 2=Medium, 3=Hard, 4=Expert
- Parses both `.chart` and `.mid` (MIDI) Clone Hero formats

### Vocal Highway
- **Pitch tubes** showing target pitch over time (Rock Band / YARC style)
- **Real-time pitch detection** from microphone via Web Audio API (autocorrelation)
- **Scrolling lyrics** synced to playback
- **Scoring**: ±1 semitone tolerance with octave equivalence
- **Visual feedback**: tubes turn green on pitch, red when off
- Parses YARC / Clone Hero `[ExpertVocals]` chart sections

### Multiplayer
- **WebSocket rooms** — create/join with room codes
- **Instrument selection**: guitar, bass, drums, vocals (one per player)
- **Auto stem muting**: each player's instrument stem is silenced so they hear themselves
- **Split-screen layout**: 2 players = top/bottom, 3-4 = grid
- **Synced playback**: 500ms time sync keeps all players aligned
- **2-second countdown** before play
- **Live chat** and **scoreboard** with per-player accuracy

### Audio Auto-Sync
When combining Clone Hero charts with CDLC audio, the pipeline automatically aligns them:
- **Cross-correlation** of audio spectrograms
- **Multi-chunk analysis** — samples 3 segments, takes median (robust against intro differences)
- **Sub-millisecond accuracy**
- No manual alignment needed

### Management Dashboard
Web GUI that wraps the entire pipeline:
- **Library tab**: searchable song grid with status badges (🎸🥁🎤🎵)
- **Process tab**: Demucs queue with progress bars, cost estimate, ETA
- **Combine tab**: click-to-pair CDLC ↔ Clone Hero with auto-match
- **Multiplayer tab**: room management
- **Settings**: API keys, directory paths, test connections

## Architecture

```
Pipeline Scripts:
  stem_separate.py      Single song → Demucs API → 5 stems
  batch_process.py      Batch library processing with resume
  create_backing.py     Generate practice mixes (no_guitar, etc.)
  combine_charts.py     Merge CDLC + Clone Hero/YARC + stems
  audio_sync.py         Cross-correlation audio alignment
  midi_parser.py        Parse .mid Clone Hero drum charts

SlopSmith Plugins:
  plugins/
    stem_toggle/        Real-time per-stem volume control
    drum_highway/       Clone Hero drum renderer + MIDI input
    vocal_highway/      Pitch-based vocal renderer + mic input
    multiplayer/        WebSocket rooms + split-screen + sync
    dashboard/          Management GUI for everything above
```

## Cost

| What | Cost |
|------|------|
| Stem separation (per song) | $0.021 |
| 100 songs | $2.10 |
| 1,000 songs | $21 |
| SlopSmith | Free |
| Clone Hero charts | Free |
| This project | Free |

## Requirements

- Python 3.10+
- Docker (for SlopSmith)
- ffmpeg (for audio processing)
- A [Replicate API token](https://replicate.com/account/api-tokens) (for Demucs)
- MIDI drum kit (optional, for drums)
- Microphone (optional, for vocals)

## Supported Formats

| Source | Format | Used For |
|--------|--------|----------|
| Rocksmith CDLC | `.psarc` | Guitar/bass charts (real frets) |
| Clone Hero | `.chart`, `.mid` | Drum charts, vocal charts |
| YARC | `.chart` | Vocal charts |
| Audio | `.mp3`, `.ogg`, `.wav`, `.flac` | Any audio for stem separation |

## Docs

- [INTEGRATION.md](INTEGRATION.md) — Full setup guide, step by step
- [PSARC_EXTRACTION.md](PSARC_EXTRACTION.md) — Extracting audio from Rocksmith files

## License

MIT

---

Built with 🦞 by [Clawd](https://github.com/rjc25) — the Conspiracy Lobster
