# TASCAM US-1800 Driver for macOS (Apple Silicon M1/M2/M3 & Intel)

Userspace high-performance driver and control suite for the **TASCAM US-1800** USB 2.0 Audio/MIDI Interface on modern macOS (macOS Monterey, Ventura, Sonoma, Sequoia+).

The original manufacturer driver was a 32/64-bit Intel-only kernel extension (`.kext`) that cannot run on Apple Silicon Macs or modern macOS versions without breaking system security. This project implements the full hardware protocol directly in userspace via **IOKit**, completely bypassing kernel limitations without requiring SIP reduction.

---

## Features & Comparison with Linux Driver

| Feature | Original Mac Driver | Initial Fork | **This Driver** |
| :--- | :--- | :--- | :--- |
| **Apple Silicon (M1/M2/M3/M4)** | ❌ Incompatible (Intel kext) | ⚠️ Partial (Output only) | ✅ **100% Native ARM64 & Intel** |
| **Recording (Inputs)** | ✅ 16 Channels | ❌ No capture | ✅ **16 Channels 24-bit (Bulk EP 0x86)** |
| **Playback (Outputs)** | ✅ 4 Channels | ⚠️ 48 kHz only, drift | ✅ **4 Channels S24_3LE (EP 0x02)** |
| **Sample Rates** | 44.1k, 48k, 88.2k, 96k | 48 kHz only | ✅ **44.1k, 48k, 88.2k, 96 kHz (Dynamic PLL)** |
| **CoreMIDI Integration** | ✅ Yes | ❌ None | ✅ **Native `TASCAM US-1800 MIDI IN / OUT`** |
| **Multi-Track WAV Recorder** | ❌ (DAW only) | ❌ None | ✅ **Built-in (16-ch poly or 16 split mono tracks)** |
| **Hardware VU / Peak Meters** | ❌ None | ❌ None | ✅ **Real-time 16-in & 4-out Peak/RMS** |
| **Web Control Panel** | ❌ None | ⚠️ Barebones | ✅ **Dark UI on `http://localhost:8420`** |

---

## Channel Mapping

### Inputs (16 Channels, 24-bit PCM)
- **Ch 1–8**: XLR Front Panel Microphone / Line Inputs (with phantom power)
- **Ch 9–10**: Front Panel Instrument (Guitar/Bass) / Line Inputs
- **Ch 11–14**: Rear Panel 1/4" Balanced Line Inputs
- **Ch 15–16**: Rear Panel S/PDIF Coaxial Digital Inputs

### Outputs (4 Channels, 24-bit PCM)
- **Ch 1–2**: Line Outs 1 & 2 / Main Monitor Outs & Headphone Jack
- **Ch 3–4**: Line Outs 3 & 4

---

## Quick Start

### 1. Build the Driver (Native ARM64 / Intel)

```bash
cd /Users/yura/.gemini/antigravity/scratch/tascam-us1800/mac-driver
DEVELOPER_DIR=/Library/Developer/CommandLineTools clang -O2 -Wall -Wextra -o tascam_usb tascam_usb.c \
  -framework IOKit -framework CoreFoundation -framework CoreMIDI -lm -lpthread
```

### 2. Run the Web Control Panel & API Server

```bash
DEVELOPER_DIR=/Library/Developer/CommandLineTools python3 tascam_api.py
```
Open **[http://localhost:8420](http://localhost:8420)** in your browser (Safari, Chrome). You will see:
- Live 16-channel hardware input meters with clip detection
- Hardware sample rate selector (44.1k, 48k, 88.2k, 96 kHz)
- 16-track session recording with instant browser download & playback
- Output test tone & multi-channel chime test
- CoreMIDI and USB connection status indicators

---

## Command Line Usage (`tascam_usb`)

```text
Usage: ./tascam_usb [options]

Options:
  --rate <hz>              Sample rate (44100, 48000, 88200, 96000, default 48000)
  --test-tone [vol]        Play 440 Hz test tone (volume 0.0 - 1.0, default 0.1)
  --chime                  Play cycling multi-channel arpeggio test chime
  --tone-ch <1-4>          Send test tone to specific output channel (default all)
  --play <file.wav>        Play 16-bit or 24-bit WAV file to outputs
  --stereo-stdin           Input 2ch stereo S24_3LE from stdin -> duplicate to 4ch
  --record <file.wav>      Record 16 channels to single 24-bit WAV file
  --record-split <prefix>  Record 16 channels to individual mono WAV files
  --capture-stdout         Stream raw 16-channel S24_3LE capture to stdout
  --channels <N>           Capture channel count for stdout (2, 8, 16, default 16)
  --no-playback            Disable playback endpoint
  --no-capture             Disable capture endpoint
  --no-midi                Disable CoreMIDI virtual ports
  --status-file <path>     Write real-time JSON stats and VU meters to file
  -h, --help               Show this help
```

### Examples

#### Output Verification (Headphones / Monitors)
```bash
# Play a 440 Hz test tone at 10% volume across all 4 outputs:
./tascam_usb --test-tone 0.10

# Play an arpeggio cycling across Output 1, Output 2, Output 3, Output 4, and All:
./tascam_usb --chime

# Play a local audio file:
./tascam_usb --play song.wav
```

#### Record All 16 Hardware Inputs to a Single 24-bit 96 kHz Multi-Track WAV
```bash
./tascam_usb --rate 96000 --record session_96k.wav
```

#### Record All 16 Channels into 16 Separate Named Mono WAV Files
```bash
./tascam_usb --rate 48000 --record-split take1
```
Creates:
- `take1_01_mic01.wav` ... `take1_08_mic08.wav`
- `take1_09_gtr09.wav` & `take1_10_gtr10.wav`
- `take1_11_line11.wav` ... `take1_14_line14.wav`
- `take1_15_spdif15.wav` & `take1_16_spdif16.wav`

#### CoreMIDI Virtual Ports
Whenever `tascam_usb` runs (or when using the Web UI/API), two virtual CoreMIDI endpoints are published system-wide:
- **`TASCAM US-1800 MIDI IN`**: Receives physical MIDI DIN packets from the hardware interface and presents them to all macOS applications (DAWs, software synths).
- **`TASCAM US-1800 MIDI OUT`**: Any MIDI events sent by your DAW are transmitted directly to the physical 5-pin DIN output on the US-1800 back panel.

---

## DAW & macOS System Audio Integration

### 1. Multi-Track DAW Recording (Logic Pro, Reaper, Ableton, Cubase, etc.)
- **Zero-Latency Direct Session:** Use the Web UI or `./tascam_usb --record-split <take>` to track your drums, band, or live gig. Drop the resulting WAV files straight into any DAW project.

### 2. Real-Time System Audio Routing (BlackHole)
To route Spotify, YouTube, or DAW stereo master output through the US-1800 outputs:
1. Install BlackHole:
   ```bash
   brew install blackhole-2ch
   ```
2. In macOS System Settings → Sound, set Output to **BlackHole 2ch**.
3. In the US-1800 Web UI (or API), start Passthrough. Audio from your Mac is captured from BlackHole and sent cleanly to your US-1800 monitors and headphones.

---

## Protocol & Architecture Notes

- **Reverse-Engineered Initialization:** Matches the Linux kernel ALSA driver (`us1800.c` by serifpersia):
  - Vendor control commands configure sampling clocks (`44100`, `48000`, `88200`, `96000`).
  - Registers `0x0d00`, `0x0d04`, `0x0e00`, `0x0f00`, `rate_reg`, `0x110b` are programmed via vendor request `0x02`.
  - Stream initialization sequence `MODE_VAL_CONFIG (0x10)` and `STREAM_START (0x32)`.
  - Clean shutdown sequence `STREAM_STOP (0x36)`.
- **Clock Dependency:** The US-1800 hardware bulk capture endpoint (EP 0x86) requires the isochronous playback stream (EP 0x02) to be active to generate frame sync pulses.
- **De-interleaving:** Raw capture packets arrive in 64-byte chunks with bit-sliced encoding across all 16 channels, decoded on the fly into 24-bit linear PCM (`S24_3LE`).
