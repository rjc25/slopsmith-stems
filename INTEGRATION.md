# SlopSmith Stems Integration Guide

Complete setup guide for the full-band multiplayer music game system built as SlopSmith plugins.

## Prerequisites

- **SlopSmith** installed and running (Docker or local)
- **Python 3.10+**
- **ffmpeg** installed and on PATH
- **MIDI drum kit** (optional, for drum highway)
- **Microphone** (optional, for vocal highway)
- **Web browser** with Web MIDI and Web Audio support (Chrome recommended)

## 1. Install Dependencies

```bash
cd slopsmith-stems
pip install -r requirements.txt
```

This installs:
- `replicate` -- Demucs API for stem separation
- `pydub` -- audio format handling
- `tqdm` -- progress bars
- `numpy` -- audio analysis and cross-correlation
- `mido` -- MIDI file parsing for .mid chart files

## 2. Set Up API Key

You need a Replicate API token for Demucs stem separation:

```bash
export REPLICATE_API_TOKEN=r8_your_token_here
```

Get one at: https://replicate.com/account/api-tokens

Cost: approximately $0.021 per song (~94 seconds processing time).

## 3. Copy Plugins into SlopSmith

Copy the plugins directory into your SlopSmith installation:

```bash
# Find your SlopSmith plugins directory
# Usually: ~/.slopsmith/plugins/ or /path/to/slopsmith/plugins/

cp -r plugins/stem_toggle /path/to/slopsmith/plugins/
cp -r plugins/drum_highway /path/to/slopsmith/plugins/
cp -r plugins/vocal_highway /path/to/slopsmith/plugins/
cp -r plugins/multiplayer /path/to/slopsmith/plugins/
```

If running SlopSmith in Docker, mount the plugins directory:

```bash
docker run -v ./plugins:/app/plugins slopsmith:latest
```

Restart SlopSmith after copying plugins.

## 4. Run the Demucs Pipeline

### Single Song

```bash
python stem_separate.py --input /path/to/song.mp3 --output /path/to/stems/
```

Accepts: `.mp3`, `.ogg`, `.wav`, `.flac`, `.psarc`

### Batch Process (entire library)

```bash
# Dry run first to see cost estimate
python batch_process.py --library /path/to/songs/ --output /path/to/stems/ --dry-run

# Process with 5 parallel workers
python batch_process.py --library /path/to/songs/ --output /path/to/stems/ --workers 5
```

### Create Backing Tracks

```bash
python create_backing.py --stems-dir /path/to/stems/
```

Creates: `no_guitar.mp3`, `no_vocals.mp3`, `no_guitar_no_vocals.mp3`, `drums_only.mp3`, etc.

## 5. Combine Charts

Combine Clone Hero/YARC drum charts with stems into unified packages:

```bash
python combine_charts.py \
    --clonehero /path/to/clonehero/song_folder/ \
    --stems /path/to/stems/song/ \
    --output /path/to/combined/song/
```

### Using MIDI charts (.mid) instead of .chart

If your Clone Hero chart uses a `.mid` file instead of `.chart`:

```bash
# Parse the MIDI file first
python midi_parser.py /path/to/notes.mid --timed --output chart_data.json

# Then combine manually or place the chart_data.json in the combined package
```

The MIDI parser handles Clone Hero's drum mapping:
- Expert drums: notes 96-100 (kick, snare, yellow, blue, green)
- Pro drum tom markers: notes 110-112
- All four difficulties (Expert/Hard/Medium/Easy)

### With CDLC guitar/bass

```bash
python combine_charts.py \
    --cdlc /path/to/song.psarc \
    --clonehero /path/to/clonehero/song_folder/ \
    --stems /path/to/stems/song/ \
    --output /path/to/combined/song/
```

Audio sync is automatic -- the combiner cross-correlates the Clone Hero audio with the stems audio to find the correct offset.

## 6. Set Up MIDI for Drums

### Hardware Setup

1. Connect your electronic drum kit via USB MIDI
2. Open SlopSmith in Chrome (Chrome has the best Web MIDI support)
3. Grant MIDI access when prompted

### MIDI Mapping

Default mapping follows General MIDI standard and works with most kits (Roland, Alesis, Yamaha).

To customize for your specific kit:

1. Go to Drum Highway settings page
2. Find the "MIDI Mapping" section
3. Click "Learn" next to a lane (Kick, Snare, Hi-Hat, Blue Tom, Floor Tom)
4. Hit the corresponding pad on your drum kit
5. The MIDI note number is captured and saved

Custom mappings are stored in `localStorage` under the key `drum_highway_midi_map` and persist across sessions.

Click "Reset to GM Default" to restore the standard mapping.

### Difficulty Selection

During gameplay, press number keys to switch difficulty:
- `1` = Easy
- `2` = Medium
- `3` = Hard
- `4` = Expert

## 7. Set Up Microphone for Vocals

### Hardware Setup

1. Connect a microphone to your computer
2. Open SlopSmith in Chrome
3. Grant microphone access when prompted

### Tips for Best Pitch Detection

- **Use headphones**: Prevents the backing track from bleeding into the mic and confusing pitch detection
- **Disable noise suppression**: Browser noise suppression can interfere with pitch detection. The vocal highway requests raw audio (no echo cancellation, no noise suppression, no auto gain)
- **Sing at a comfortable volume**: Too quiet and the detector cannot distinguish pitch from noise
- **Octave equivalence**: The scorer accepts singing an octave above or below the target pitch

### How Scoring Works

- Blue pitch tubes show the target pitch and timing
- An orange indicator dot shows your detected pitch in real-time
- Tubes turn green when you match the pitch within +/-1 semitone
- Tubes turn red when you are off-pitch
- The vocals stem is automatically muted when the vocal highway is active

## 8. Start a Multiplayer Session

### Creating a Room

1. Click "Multiplayer" in the SlopSmith navigation
2. Enter your player name
3. Click "Create Room"
4. Share the room code with your bandmates

### Joining a Room

1. Open Multiplayer
2. Enter your name
3. Either click "Join" on a listed room, or enter the room code

### Playing Together

1. Each player picks an instrument (guitar, bass, drums, vocals)
2. Each player clicks "Ready Up"
3. The host clicks "Start Game"
4. A 2-second countdown begins
5. Playback starts synchronized across all players
6. Each player's instrument stem is automatically muted for them

### Split-Screen Layout

- 1 player: full screen
- 2 players: top/bottom split
- 3-4 players: 2x2 grid

### Time Sync

The host is the time authority. Every 500ms, the server requests the host's current playback time and broadcasts it to all other players. Clients correct their playback position if drift exceeds 100ms.

For tightest sync, play on the same local network.

## Troubleshooting

### Stems

| Problem | Solution |
|---------|----------|
| "REPLICATE_API_TOKEN not set" | `export REPLICATE_API_TOKEN=r8_...` |
| Stems sound wrong or garbled | Re-run Demucs; delete existing stems first |
| No stems toggle UI appears | Check that stem_toggle plugin is loaded in SlopSmith settings |
| Audio out of sync | Use `audio_sync.py` to find the offset, or re-run combine_charts.py |

### MIDI

| Problem | Solution |
|---------|----------|
| "No MIDI" in drum highway | Check USB connection; try refreshing the page |
| Wrong pad triggers wrong lane | Use MIDI Learn in settings to remap |
| Hits not registering | Check the hit window; try Hard difficulty (wider window) |
| MIDI works in settings but not gameplay | Ensure the drum highway visualization is selected |

### Vocals

| Problem | Solution |
|---------|----------|
| "No Mic" displayed | Grant microphone permission in browser settings |
| Pitch detection erratic | Use headphones; move away from speakers |
| Pitch always shows wrong note | Check microphone input level in system settings |
| No vocal chart data | The song's .chart file may not have a vocals section |

### Multiplayer

| Problem | Solution |
|---------|----------|
| Cannot connect to room | Check that WebSocket connections are not blocked by firewall |
| Players see different songs | All players must have the same song loaded in SlopSmith |
| Audio drift during play | Ensure all players have stable network; try same LAN |
| Room disappears | Empty rooms auto-delete after 5 minutes |

## File Layout After Setup

```
slopsmith-stems/
  plugins/
    stem_toggle/         -- Stem mute/unmute with keyboard shortcuts
    drum_highway/        -- Clone Hero drum visualization + MIDI input
    vocal_highway/       -- YARC-style pitch highway + mic input
    multiplayer/         -- WebSocket multiplayer rooms
  combine_charts.py      -- Merge charts + stems into packages
  midi_parser.py         -- Parse .mid files for Clone Hero drums
  audio_sync.py          -- Cross-correlate audio for timing alignment
  stem_separate.py       -- Demucs stem separation via Replicate API
  create_backing.py      -- Generate practice backing tracks
  batch_process.py       -- Batch process entire song libraries
```
