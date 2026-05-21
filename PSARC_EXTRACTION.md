# PSARC Extraction Notes

## What is PSARC?

PSARC (PlayStation Archive) is a container format originally developed by Sony. Rocksmith and Rocksmith CDLC (Custom DLC) use it to package song data, including audio, arrangements, tone definitions, and metadata.

## File Structure

A `.psarc` file contains:

- **Audio**: Usually in `.wem` (Wwise Encoded Media) format, sometimes `.ogg`
- **Arrangements**: XML files defining guitar/bass tab data
- **Tone definitions**: JSON/XML for amp and effect settings
- **Album art**: DDS texture files
- **Manifest**: JSON metadata (song name, artist, tuning, etc.)

## Compression

PSARC files use **zlib-compressed entries**. The archive header specifies:
- Magic bytes: `PSAR`
- Table of contents with entry offsets and sizes
- Each entry is compressed in blocks (typically 64KB)
- Block sizes are stored as 16-bit big-endian values

Our `stem_separate.py` includes a binary PSARC parser (`extract_audio_from_psarc_binary()`) that handles the zlib decompression.

## Audio Format Challenge

The main challenge is that Rocksmith audio is in **Wwise `.wem` format**, which is not directly playable. Converting `.wem` to a standard format requires:

### Option 1: vgmstream
- Open-source decoder that handles `.wem` and many other game audio formats
- Available as `vgmstream-cli` or as an foobar2000 plugin
- Usage: `vgmstream-cli -o output.wav input.wem`

### Option 2: ww2ogg + revorb
- `ww2ogg` converts Wwise `.wem` to `.ogg` (Vorbis)
- `revorb` fixes the Ogg page structure after conversion
- Usage: `ww2ogg input.wem --pcb packed_codebooks_aoTuV_603.bin && revorb input.ogg`

### Option 3: Rocksmith Custom Song Toolkit (RCST)
- **Recommended approach**: GUI tool specifically designed for Rocksmith CDLC
- Extracts all assets from `.psarc` files
- Automatically converts audio to `.ogg`
- Available at: https://github.com/catara/rocksmith-custom-song-toolkit
- Cross-platform via Mono on Linux/macOS

## Recommended Workflow

### If you have raw `.psarc` files:

1. **Use RCST to extract audio first**:
   ```
   # Extract .psarc to a folder with .ogg audio
   RocksmithCustomSongToolkit.exe --extract song_p.psarc --output ./extracted/
   ```

2. **Feed the extracted `.ogg` to the stems pipeline**:
   ```bash
   python stem_separate.py --input ./extracted/song.ogg --output ./stems/song/
   ```

### If you have pre-extracted audio:

Our `combine_charts.py` already accepts `.ogg`, `.mp3`, `.wav`, and `.flac` as input -- no PSARC extraction needed:
```bash
python combine_charts.py \
    --stems /path/to/stems/song/ \
    --clonehero /path/to/clonehero/song/ \
    --output /path/to/combined/song/
```

### If using SlopSmith's built-in extraction:

SlopSmith has built-in PSARC handling that automates the full pipeline:
1. Extracts audio from `.psarc`
2. Converts `.wem` to a playable format
3. Routes through the Demucs stems pipeline

This is the easiest path if you are already running SlopSmith.

## Rocksmith CDLC Encryption

Official Rocksmith DLC and some CDLC use an **encryption key** on the `.psarc` archive. The key is well-known in the community:

- The PSARC TOC (table of contents) may be AES-256 encrypted
- RCST and other community tools handle decryption automatically
- Our binary parser (`extract_audio_from_psarc_binary`) handles unencrypted archives only
- For encrypted archives, use RCST to extract first

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ffmpeg` cannot read `.wem` | Install vgmstream or use RCST to convert first |
| PSARC extraction returns empty | Archive may be encrypted -- use RCST |
| Audio is garbled after extraction | Try `revorb` on the `.ogg` output to fix page structure |
| "No audio found" error | Check that the `.psarc` contains audio entries (some are arrangement-only) |
| Multiple audio files extracted | Rocksmith often has separate preview/full tracks -- use the larger file |
