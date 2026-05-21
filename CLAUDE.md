# CLAUDE.md — AI Agent Guide for SlopSmith Stems

## Project Overview

SlopSmith Stems is a full-band music practice platform built as plugins for [SlopSmith](https://github.com/byrongamatos/slopsmith). It adds stem separation (Demucs), Clone Hero drum chart support, vocal pitch detection, multiplayer, and a management dashboard.

## Architecture

```
slopsmith-stems/
├── Pipeline Scripts (standalone Python, run from CLI or dashboard)
│   ├── stem_separate.py      Demucs API wrapper — single song processing
│   ├── batch_process.py      Parallel batch processor with resume/progress
│   ├── create_backing.py     Mix stems into practice tracks (no_guitar, etc.)
│   ├── combine_charts.py     Merge CDLC + Clone Hero/YARC + stems
│   ├── audio_sync.py         Cross-correlation spectrogram alignment
│   └── midi_parser.py        Clone Hero .mid drum chart parser
│
├── plugins/                   SlopSmith plugin directory
│   ├── stem_toggle/           Real-time per-stem mute/unmute
│   ├── drum_highway/          Clone Hero drum renderer + MIDI input
│   ├── vocal_highway/         Pitch highway + mic detection
│   ├── multiplayer/           WebSocket rooms + split-screen
│   └── dashboard/             Management GUI wrapping all scripts
│
├── INTEGRATION.md             Full setup guide
├── PSARC_EXTRACTION.md        Rocksmith audio extraction reference
└── requirements.txt           Python dependencies
```

## Key Decisions

1. **Demucs via Replicate API** — avoids local GPU requirement. $0.021/song. Could self-host for free with a GPU but Replicate is simpler for most users.

2. **Plugin architecture** — everything is a SlopSmith plugin following the plugin.json manifest pattern. Plugins are independent and compose via events (stems:mute, stems:unmute).

3. **Vanilla JS frontend** — SlopSmith uses no frameworks. All plugins must be vanilla JS + HTML + Tailwind CSS. No React, no Vue, no build step.

4. **FastAPI backend** — matches SlopSmith's backend. Plugin routes export `setup(app, context)`.

5. **Cross-correlation for sync** — aligns Clone Hero audio to CDLC audio using numpy spectrogram cross-correlation. No ML needed.

6. **Web MIDI API** — browser-native MIDI for drum kit input. No drivers or middleware needed.

7. **Autocorrelation pitch detection** — time-domain pitch detection from microphone. Runs in browser via Web Audio API. No server-side processing.

## Plugin Contracts

All plugins follow SlopSmith's plugin system (see main SlopSmith CLAUDE.md):

- `plugin.json` — manifest with id, name, type, nav, screen, script, routes
- `routes.py` — `setup(app, context)` function, FastAPI endpoints
- `screen.js` — vanilla JS in IIFE, runs in global scope
- `screen.html` / `settings.html` — HTML fragments loaded by SlopSmith

### Cross-Plugin Communication

Plugins communicate via SlopSmith's event emitter:

```javascript
// Stem Toggle listens for:
window.slopsmith.on('stems:mute', ({stem}) => { ... });
window.slopsmith.on('stems:unmute', ({stem}) => { ... });
window.slopsmith.on('stems:set', ({stem, active}) => { ... });

// Drum Highway emits on load:
window.slopsmith.emit('stems:mute', {stem: 'drums'});

// Vocal Highway emits on load:
window.slopsmith.emit('stems:mute', {stem: 'vocals'});

// Multiplayer sends per-player stem config on game start
```

### Visualization Plugins

drum_highway and vocal_highway are `type: "visualization"` plugins. They export renderer factories:

```javascript
window.slopsmithViz_drum_highway = function() {
    return { contextType: '2d', init(canvas, bundle) {}, draw(bundle) {}, resize(w,h) {}, destroy() {} };
};
```

## Data Flow

```
1. User has CDLC songs (.psarc) and Clone Hero songs (folders with notes.chart)

2. Dashboard scans both libraries → builds library.json index

3. User clicks "Process Stems" → Demucs API separates audio into 5 stems

4. User pairs CDLC ↔ Clone Hero songs → combine_charts.py:
   a. Extracts audio from both sources
   b. audio_sync.py finds time offset via cross-correlation
   c. Applies offset to drum chart timings
   d. Packages everything into a combined folder

5. During playback:
   a. SlopSmith loads CDLC chart for guitar/bass highway
   b. drum_highway loads Clone Hero drum chart
   c. vocal_highway loads vocal chart
   d. stem_toggle loads all 5 stems as parallel Audio elements
   e. Each plugin emits stems:mute for its instrument
   f. Player hears full band minus their part

6. Multiplayer:
   a. Host creates WebSocket room, picks a song
   b. Players join, pick instruments
   c. Each player's stem auto-mutes
   d. Synced countdown → all start together
   e. 500ms time sync keeps everyone aligned
```

## File Formats

### Clone Hero .chart Drum Mapping
```
Expert drums: notes 0-5 in [ExpertDrums] section
  0 = kick, 1 = red/snare, 2 = yellow, 3 = blue, 4 = orange, 5 = green
```

### Clone Hero .mid Drum Mapping
```
Expert drums: MIDI notes 96-100
  96 = kick, 97 = red/snare, 98 = yellow, 99 = blue, 100 = green
Pro drum tom markers: 110 = yellow tom, 111 = blue tom, 112 = green tom
```

### MIDI Drum Kit (General MIDI)
```
36/35 = kick, 38/40 = snare, 42/46/44 = hi-hat
48/47/50 = blue tom, 45/43/41 = green tom
49/57 = crash, 51/59 = ride
```

### Combined Package Structure
```
combined/song_name/
  manifest.json          Sources and sync metadata
  cdlc/                  Original PSARC or extracted files
  drums/
    chart_data.json      Parsed drum notes with timing (seconds)
    notes.chart          Original Clone Hero chart (reference)
  stems/
    drums.mp3
    bass.mp3
    vocals.mp3
    guitar.mp3
    other.mp3
```

## Testing

```bash
# Test stem separation on a single song
python stem_separate.py --input song.mp3 --output /tmp/test_stems/

# Test chart parsing
python -c "from combine_charts import parse_chart_file; print(parse_chart_file('notes.chart'))"

# Test audio sync
python audio_sync.py audio_a.mp3 audio_b.mp3 --chunked

# Test MIDI parsing
python midi_parser.py notes.mid --output test_drums.json
```

## Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| Demucs returns error | Invalid API key | Check REPLICATE_API_TOKEN env var |
| No stems for a song | Song not processed yet | Run stem_separate.py or use dashboard |
| Drum chart timing off | Different audio sources | Re-run combine with --force-sync |
| MIDI kit not detected | Browser permissions | Allow MIDI access when prompted |
| Mic not working | Browser permissions | Allow microphone access, use HTTPS |
| Multiplayer desync | Network latency | Reduce sync interval in routes.py |
| PSARC extraction fails | Encrypted audio | Use Rocksmith Custom Song Toolkit first |
