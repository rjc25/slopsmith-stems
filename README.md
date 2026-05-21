# SlopSmith Stems

Demucs stem separation pipeline for Rocksmith CDLC songs. Separates your entire library into individual stems (drums, bass, vocals, guitar, other) so you can toggle any instrument on/off while practicing in SlopSmith.

## What it does

1. Extracts audio from your Rocksmith CDLC `.psarc` files
2. Sends each song through [Demucs](https://github.com/facebookresearch/demucs) (AI stem separation)
3. Outputs individual stems: `drums.mp3`, `bass.mp3`, `vocals.mp3`, `guitar.mp3`, `other.mp3`
4. Optionally creates a `no_guitar.mp3` backing track for quick practice
5. Packages stems for use with SlopSmith's stem mixer

## Cost

- ~$0.021 per song via Replicate API
- 1,000 songs = ~$21
- ~94 seconds per song processing time

## Setup

```bash
pip install -r requirements.txt
```

You need a [Replicate API token](https://replicate.com/account/api-tokens):
```bash
export REPLICATE_API_TOKEN=r8_your_token_here
```

## Usage

### Process a single song
```bash
python stem_separate.py --input /path/to/song.psarc --output /path/to/output/
```

### Batch process entire library
```bash
python batch_process.py --library /path/to/cdlc/ --output /path/to/stems/ --workers 5
```

### Create backing tracks (no guitar)
```bash
python create_backing.py --stems-dir /path/to/stems/
```

### Convert to SlopSmith Sloppak format
```bash
python to_sloppak.py --stems-dir /path/to/stems/ --cdlc-dir /path/to/cdlc/
```

## Toggle Controls (in SlopSmith)

Once stems are loaded, use keyboard shortcuts:
- `G` — Toggle guitar on/off
- `V` — Toggle vocals on/off
- `B` — Toggle bass on/off
- `D` — Solo drums
- `A` — All stems on (reset)

## Pipeline Overview

```
CDLC (.psarc)
    |
    v
[Extract Audio] (.ogg/.wem -> .mp3)
    |
    v
[Demucs via Replicate API]
    |
    v
stems/
  drums.mp3
  bass.mp3
  vocals.mp3
  guitar.mp3
  other.mp3
  no_guitar.mp3  (drums+bass+vocals+other mixed)
    |
    v
[Package as Sloppak] (optional)
```

## License

MIT
