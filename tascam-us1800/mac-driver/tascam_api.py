#!/usr/bin/env python3
"""
FastAPI backend for TASCAM US-1800 control panel.
Runs the C driver as a subprocess and exposes REST + WebSocket APIs.

Usage: python3 tascam_api.py
Server runs on port 8420

Logs: stderr at INFO by default. TASCAM_LOG_LEVEL=DEBUG for WebSocket volume lines.
Optional TASCAM_LOG_FILE=api.log (relative → next to this script) duplicates logs to a file.

Passthrough: each session prepends a short quality checklist to capture.log (same ideas as GET /api/audio-tips).
Defaults and TASCAM_* env reference: GET /api/passthrough-config (passthrough_config.py).
"""
import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
import uvicorn

from passthrough_config import (
    API_PASSTHROUGH_DEFAULTS,
    virtual_audio_device_name,
    DEVICE_PCM_HZ,
    POST_START_HEALTH_WAIT_SEC,
    ROUTING_DELAY_FALLBACK_SEC,
    USB_PCM_CHANNELS,
    apply_tascam_capture_env_aliases,
    default_capture_queue_blocks_str,
    passthrough_config_response,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_DIR = os.path.join(SCRIPT_DIR, "recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)
LIVE_GAIN_FILE = os.path.join(SCRIPT_DIR, "live_capture_gain.txt")
C_BINARY = os.path.join(SCRIPT_DIR, "tascam_usb")
C_SOURCE = os.path.join(SCRIPT_DIR, "tascam_usb.c")
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
SWITCH_AUDIO = shutil.which("SwitchAudioSource") or "SwitchAudioSource"

# US-1800 isoch path in tascam_usb.c assumes 48 kHz packet timing; other rates can be silent.
DEVICE_PCM_RATE = DEVICE_PCM_HZ
# Passthrough defaults and env aliases: passthrough_config.py (GET /api/passthrough-config).
# PortAudio capture is default; TASCAM_PASSTHROUGH_CAPTURE=avfoundation uses FFmpeg.
# TASCAM_VIRTUAL_AUDIO_DEVICE — CoreAudio name for route + capture (default BlackHole 2ch).
# TASCAM_BLACKHOLE_OUTPUT_VOLUME=0–100 — macOS output level when switching to that device.

# Generic digital-audio hygiene (also written to capture.log on each passthrough start).
PASSTHROUGH_QUALITY_TIPS = [
    "Match sample rate end-to-end (system output, virtual input device, capture, final USB device) when you can — avoids stacked resamplers.",
    "Virtual devices (BlackHole, Loopback, VB-Cable, …) add CoreAudio buffering. Low latency: CAPTURE_LATENCY_AUTO=1 (default). BlackHole tunables: https://github.com/ExistentialAudio/BlackHole/wiki/Adjust-Driver-Latency",
    "Fewer hops: every extra app or virtual device in the chain adds buffering, jitter, and conversion risk.",
    "If you hear clicks or dropouts, favor stability: larger I/O buffers in the player or DAW (trade latency).",
    "Passthrough: after a bad spell, search capture.log and tascam.log for lines tagged [glitch] (overflows, queue drops, underruns, latency_trim).",
    "Use direct USB to the hardware; flaky hubs and aggressive background CPU/GPU load cause glitches.",
    "When resampling is unavoidable, one high-quality conversion beats several cheap ones.",
]


def _write_passthrough_quality_preamble(capture_log):
    """Long checklist only if TASCAM_CAPTURE_QUALITY_PREAMBLE=1 (keeps capture.log readable)."""
    if os.environ.get("TASCAM_CAPTURE_QUALITY_PREAMBLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        capture_log.write("# --- Passthrough quality checklist (generic) ---\n")
        for i, tip in enumerate(PASSTHROUGH_QUALITY_TIPS, 1):
            capture_log.write(f"# {i}. {tip}\n")
        capture_log.write("# " + "-" * 60 + "\n\n")
    else:
        capture_log.write(
            "# Tips: GET /api/audio-tips — full list in log if TASCAM_CAPTURE_QUALITY_PREAMBLE=1\n\n"
        )


# ffmpeg aresample: use SoX engine when built in, else high-quality SWResampler (Homebrew often lacks soxr).
_resampler_engine_checked = None


def _passthrough_macos_output_volume():
    try:
        v = int(os.environ.get("TASCAM_BLACKHOLE_OUTPUT_VOLUME", "100"))
    except ValueError:
        return 100
    return max(0, min(100, v))


def _passthrough_aresample_ffmpeg_args(target_rate=None):
    """Return (list of -af and filter arg..., engine label for logs/API)."""
    global _resampler_engine_checked
    tr = int(target_rate) if target_rate is not None else DEVICE_PCM_RATE
    if tr < 44100 or tr > 192000:
        tr = DEVICE_PCM_RATE

    if _resampler_engine_checked is None:
        try:
            r = subprocess.run(
                [
                    FFMPEG,
                    "-nostdin",
                    "-hide_banner",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=stereo",
                    "-t",
                    "0.01",
                    "-af",
                    f"aresample={DEVICE_PCM_RATE}:resampler=soxr:precision=28",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                timeout=8,
            )
            _resampler_engine_checked = "soxr" if r.returncode == 0 else "swr-hq"
        except Exception:
            _resampler_engine_checked = "swr-hq"

    if _resampler_engine_checked == "soxr":
        return (
            ["-af", f"aresample={tr}:resampler=soxr:precision=28:cheby=1"],
            "soxr",
        )
    return (
        [
            "-af",
            f"aresample={tr}:filter_size=64:kaiser_beta=12"
            f":linear_interp=0:filter_type=blackman_nuttall",
        ],
        "swr-hq",
    )


def _ffmpeg_aresample_filter_for_input_rate(capture_sr: int, target_rate=None):
    """aresample chain for capture_sr → target output rate (default DEVICE_PCM_RATE)."""
    tr = int(target_rate) if target_rate is not None else DEVICE_PCM_RATE
    if tr < 44100 or tr > 192000:
        tr = DEVICE_PCM_RATE
    af_args, label = _passthrough_aresample_ffmpeg_args(tr)
    filt = af_args[1]
    # in_sample_rate helps SWResampler when lavfi guesses wrong; it breaks resampler=soxr on many ffmpeg builds.
    if label == "swr-hq":
        needle = f"aresample={tr}:"
        if needle in filt:
            filt = filt.replace(
                needle,
                f"aresample={tr}:in_sample_rate={capture_sr}:",
                1,
            )
    return filt, label


def _avfoundation_audio_index():
    """FFmpeg AVFoundation index for virtual_audio_device_name() (avoids PortAudio capture)."""
    name = virtual_audio_device_name()
    try:
        r = subprocess.run(
            [
                FFMPEG,
                "-hide_banner",
                "-f",
                "avfoundation",
                "-list_devices",
                "true",
                "-i",
                "",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        text = r.stderr
        in_audio = False
        candidates = []

        for line in text.splitlines():
            if "AVFoundation audio devices:" in line:
                in_audio = True
                continue
            if "AVFoundation video devices:" in line:
                in_audio = False
                continue
            if in_audio and name in line:
                m = re.search(r"\[(\d+)\]", line)
                if m:
                    candidates.append((int(m.group(1)), line))
        if candidates:
            return candidates[0][0]
        # Substring fallback (device name truncated in list)
        token = name.split()[0]
        if token:
            in_audio = False
            for line in text.splitlines():
                if "AVFoundation audio devices:" in line:
                    in_audio = True
                    continue
                if "AVFoundation video devices:" in line:
                    in_audio = False
                    continue
                if in_audio and token in line:
                    m = re.search(r"\[(\d+)\]", line)
                    if m:
                        return int(m.group(1))
    except Exception:
        pass
    return 0


def blackhole_input_sample_rate():
    """Native sample rate of the virtual input device after macOS has routed to it.

    Forcing a different PortAudio capture rate makes CoreAudio resample in the
    callback (bad: metallic / "double voice" artifacts). Match this to the stream.
    """
    try:
        import sounddevice as sd

        d = sd.query_devices(virtual_audio_device_name(), kind="input")
        sr = int(float(d["default_samplerate"]))
        if sr < 8000 or sr > 192000:
            return _virtual_audio_fallback_sample_rate()
        return sr
    except Exception:
        return _virtual_audio_fallback_sample_rate()


def _virtual_audio_fallback_sample_rate():
    try:
        import sounddevice as sd

        target = virtual_audio_device_name().lower()
        hostapis = sd.query_hostapis()
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] < 2:
                continue
            name = d["name"]
            if target not in name.lower():
                continue
            api = hostapis[d["hostapi"]]["name"]
            if "core" not in api.lower():
                continue
            sr = int(float(d["default_samplerate"]))
            if 8000 <= sr <= 192000:
                return sr
    except Exception:
        pass
    return 48000


def _configure_logging():
    """stderr always; optional file via TASCAM_LOG_FILE (relative paths → SCRIPT_DIR)."""
    root = logging.getLogger("tascam_api")
    if root.handlers:
        return
    level_name = os.environ.get("TASCAM_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [tascam_api] %(message)s",
        datefmt="%H:%M:%S",
    )
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    path = (os.environ.get("TASCAM_LOG_FILE") or "").strip()
    if path:
        full = path if os.path.isabs(path) else os.path.join(SCRIPT_DIR, path)
        fh = logging.FileHandler(full, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


_configure_logging()
LOG = logging.getLogger("tascam_api")


async def _stream_supervisor():
    """Stop passthrough/tone if tascam_usb exits or UI would show Disconnected (get_stats)."""
    interval = 0.75
    try:
        while True:
            await asyncio.sleep(interval)
            proc = driver_proc
            if proc is None or stream_mode is None:
                continue
            if proc.poll() is not None:
                LOG.warning(
                    "auto-stop: tascam_usb exited (returncode=%s)",
                    proc.returncode,
                )
                await stop()
                continue
            # Same fields as WebSocket /api/status → StatusBar (device_connected + streaming).
            st = get_stats()
            if st.get("streaming") and not st.get("device_connected"):
                LOG.warning("auto-stop: device disconnected (device_connected=false while streaming)")
                await stop()
    except asyncio.CancelledError:
        raise


@asynccontextmanager
async def _app_lifespan(_: FastAPI):
    LOG.info(
        "startup (8420); TASCAM_LOG_LEVEL=%s; TASCAM_LOG_FILE=%s",
        os.environ.get("TASCAM_LOG_LEVEL", "INFO"),
        os.environ.get("TASCAM_LOG_FILE") or "(stderr only)",
    )
    supervisor = asyncio.create_task(_stream_supervisor())
    try:
        yield
    finally:
        supervisor.cancel()
        try:
            await supervisor
        except asyncio.CancelledError:
            pass
        try:
            await stop()
        except Exception:
            LOG.exception("shutdown: stop() failed")
        LOG.info("shutdown")


app = FastAPI(lifespan=_app_lifespan)

driver_proc = None
stream_start_time = None
stream_config = {}
stream_mode = None  # "tone", "passthrough", "recording", or None after stop
ws_clients = set()

recording_proc = None
recording_file = None
recording_start_time = None

ffmpeg_proc = None
passthrough_resample_proc = None
gain_bridge_proc = None
passthrough_quad_proc = None
saved_audio_output = None
saved_volume = None


def is_device_connected():
    try:
        r = subprocess.run(
            ["ioreg", "-p", "IOUSB", "-w0"],
            capture_output=True, text=True, timeout=3,
        )
        return "US-1800" in r.stdout
    except Exception:
        return False


def build_driver():
    if (os.path.exists(C_BINARY) and
        os.path.getmtime(C_BINARY) > os.path.getmtime(C_SOURCE)):
        return True
    build_env = os.environ.copy()
    build_env.setdefault("DEVELOPER_DIR", "/Library/Developer/CommandLineTools")
    r = subprocess.run([
        "clang", "-O2", "-o", C_BINARY, C_SOURCE,
        "-framework", "IOKit", "-framework", "CoreFoundation", "-framework", "CoreMIDI",
        "-lm", "-lpthread",
        "-Wall", "-Wno-deprecated-declarations",
    ], env=build_env)
    if r.returncode != 0:
        LOG.error("clang build failed (rc=%s) tascam_usb.c", r.returncode)
    return r.returncode == 0


def get_current_audio_output():
    try:
        r = subprocess.run([SWITCH_AUDIO, "-c"], capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except Exception:
        return None


def set_audio_output(name):
    try:
        subprocess.run([SWITCH_AUDIO, "-s", name], capture_output=True, timeout=3)
    except Exception:
        pass


def get_output_volume():
    try:
        r = subprocess.run(
            ["osascript", "-e", "output volume of (get volume settings)"],
            capture_output=True, text=True, timeout=3,
        )
        return int(r.stdout.strip())
    except Exception:
        return None


def set_output_volume(vol):
    try:
        subprocess.run(
            ["osascript", "-e", f"set volume output volume {vol}"],
            capture_output=True, timeout=3,
        )
    except Exception:
        pass


def write_live_gain(vol: float):
    """Software gain for passthrough; read by capture_blackhole from LIVE_GAIN_FILE."""
    global stream_config
    vol = max(0.0, min(1.0, float(vol)))
    try:
        with open(LIVE_GAIN_FILE, "w") as f:
            f.write(f"{vol:.8f}\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    sc = dict(stream_config) if stream_config else {}
    sc["volume"] = vol
    stream_config = sc


def _nominal_s24le_packed_bps(cfg: dict) -> int:
    """Bytes/s for S24_3LE packed PCM: sample_rate × channels × 3 (not measured USB throughput)."""
    try:
        ch = int(cfg.get("channels", USB_PCM_CHANNELS))
        sr = int(cfg.get("sample_rate", 48000))
    except (TypeError, ValueError):
        ch, sr = 2, 48000
    ch = max(1, min(32, ch))
    if sr < 8000 or sr > 384000:
        sr = 48000
    return sr * ch * 3


def get_stats():
    streaming = driver_proc is not None and driver_proc.poll() is None
    rec_active = recording_proc is not None and recording_proc.poll() is None
    elapsed = time.time() - stream_start_time if (streaming and stream_start_time) else 0

    status_json_path = "/tmp/tascam_status.json"
    in_peak = [0.0] * 16
    out_peak = [0.0] * 4
    midi_rx = 0
    midi_tx = 0
    captured_frames = 0
    hw_rate = stream_config.get("sample_rate", 48000)

    if os.path.exists(status_json_path):
        try:
            with open(status_json_path, "r") as sf:
                sdata = json.load(sf)
                in_peak = sdata.get("in_peak", [0.0] * 16)
                out_peak = sdata.get("out_peak", [0.0] * 4)
                midi_rx = sdata.get("midi_rx", 0)
                midi_tx = sdata.get("midi_tx", 0)
                captured_frames = sdata.get("captured_frames", 0)
                if "sample_rate" in sdata:
                    hw_rate = sdata["sample_rate"]
        except Exception:
            pass

    effective_mode = "recording" if rec_active else stream_mode
    active_any = streaming or rec_active
    rec_elapsed = round(time.time() - recording_start_time, 1) if (rec_active and recording_start_time) else 0

    out = {
        "device_connected": is_device_connected(),
        "streaming": active_any,
        "mode": effective_mode,
        "firmware": "1.00",
        "sample_rate": hw_rate if active_any else None,
        "channels": 16 if rec_active else (stream_config.get("channels") if streaming else None),
        "output_level": stream_config.get("volume", 0.01) if streaming else None,
        "elapsed": round(elapsed, 1),
        "output_rate": _nominal_s24le_packed_bps(stream_config) if streaming else 0,
        "buffer_fill": 85 if streaming else 0,
        "underruns": 0,
        "in_peak": in_peak,
        "out_peak": out_peak,
        "midi_rx": midi_rx,
        "midi_tx": midi_tx,
        "captured_frames": captured_frames,
        "is_recording": rec_active,
        "recording_file": os.path.basename(recording_file) if recording_file else None,
        "recording_elapsed": rec_elapsed,
    }
    if stream_mode == "passthrough" and stream_config:
        out["passthrough_lab"] = {
            "capture_backend": stream_config.get("capture_backend"),
            "capture_sample_rate": stream_config.get("capture_sample_rate"),
            "capture_sr_forced": stream_config.get("capture_sr_forced"),
            "resampler": stream_config.get("resampler"),
            "macos_output_volume": stream_config.get("macos_output_volume"),
            "routing_delay_sec": stream_config.get("routing_delay_sec"),
            "capture_blocksize": stream_config.get("capture_blocksize"),
            "capture_backend_request": stream_config.get("capture_backend_request"),
            "virtual_audio_device": stream_config.get("virtual_audio_device"),
        }
    return out


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = os.path.join(SCRIPT_DIR, "static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>TASCAM US-1800 Driver Running</h1><p><a href='/docs'>API Documentation</a></p>")


@app.get("/api/status")
async def status():
    return get_stats()


@app.get("/api/audio-tips")
async def audio_tips():
    """Short checklist echoed in capture.log at passthrough start (for UI or bookmarks)."""
    return {"tips": PASSTHROUGH_QUALITY_TIPS}


@app.get("/api/passthrough-config")
async def passthrough_config_endpoint():
    """Passthrough POST defaults and TASCAM_* tuning reference for UI or scripts."""
    return passthrough_config_response()


@app.get("/api/sample-rates")
async def get_sample_rates():
    return {"rates": [44100, 48000, 88200, 96000], "default": 48000}


@app.post("/api/record/start")
async def record_start(config: dict = {}):
    global recording_proc, recording_file, recording_start_time
    if recording_proc and recording_proc.poll() is None:
        return JSONResponse({"error": "Recording already in progress"}, 400)

    if driver_proc and driver_proc.poll() is None:
        await stop()

    if not build_driver():
        return JSONResponse({"error": "Build failed"}, 500)

    rate = int(config.get("sample_rate", 48000))
    if rate not in (44100, 48000, 88200, 96000):
        rate = 48000

    ts = time.strftime("%Y%m%d_%H%M%S")
    custom_name = config.get("name", "").strip()
    prefix = f"{custom_name}_" if custom_name else ""
    filename = f"rec_{prefix}{ts}.wav"
    filepath = os.path.join(RECORDINGS_DIR, filename)

    cmd = [
        C_BINARY,
        "--rate", str(rate),
        "--record", filepath,
        "--status-file", "/tmp/tascam_status.json",
    ]
    recording_proc = subprocess.Popen(cmd, stderr=subprocess.PIPE)
    recording_file = filepath
    recording_start_time = time.time()
    LOG.info("record_start: %s rate=%d", filepath, rate)
    return {
        "status": "recording",
        "filename": filename,
        "sample_rate": rate,
        "channels": 16,
    }


@app.post("/api/record/stop")
async def record_stop():
    global recording_proc, recording_file, recording_start_time
    if not recording_proc or recording_proc.poll() is not None:
        return {"status": "not_recording"}

    recording_proc.send_signal(signal.SIGINT)
    try:
        recording_proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        recording_proc.kill()

    saved_file = recording_file
    elapsed = round(time.time() - recording_start_time, 1) if recording_start_time else 0
    recording_proc = None
    recording_file = None
    recording_start_time = None

    size_mb = 0.0
    if saved_file and os.path.exists(saved_file):
        size_mb = round(os.path.getsize(saved_file) / (1024 * 1024), 2)

    return {
        "status": "stopped",
        "filename": os.path.basename(saved_file) if saved_file else None,
        "duration_sec": elapsed,
        "size_mb": size_mb,
    }


@app.get("/api/recordings")
async def list_recordings():
    recs = []
    if os.path.exists(RECORDINGS_DIR):
        for f in sorted(os.listdir(RECORDINGS_DIR), reverse=True):
            if f.endswith(".wav"):
                p = os.path.join(RECORDINGS_DIR, f)
                try:
                    st = os.stat(p)
                    size_mb = round(st.st_size / (1024 * 1024), 2)
                    est_sec = round((st.st_size - 44) / (16 * 3 * 48000), 1) if st.st_size > 44 else 0
                    recs.append({
                        "filename": f,
                        "size_mb": size_mb,
                        "date": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                        "duration_sec": max(0, est_sec),
                    })
                except OSError:
                    pass
    return {"recordings": recs}


@app.get("/api/recordings/{filename}")
async def get_recording(filename: str):
    safe_name = os.path.basename(filename)
    path = os.path.join(RECORDINGS_DIR, safe_name)
    if not os.path.exists(path):
        return JSONResponse({"error": "File not found"}, 404)
    return FileResponse(path, media_type="audio/wav", filename=safe_name)


@app.delete("/api/recordings/{filename}")
async def delete_recording(filename: str):
    safe_name = os.path.basename(filename)
    path = os.path.join(RECORDINGS_DIR, safe_name)
    if os.path.exists(path):
        try:
            os.remove(path)
            return {"status": "deleted", "filename": safe_name}
        except OSError as e:
            return JSONResponse({"error": str(e)}, 500)
    return JSONResponse({"error": "File not found"}, 404)


@app.post("/api/stop")
async def stop():
    global driver_proc, stream_start_time, stream_mode, stream_config, ffmpeg_proc
    global passthrough_resample_proc, gain_bridge_proc, passthrough_quad_proc
    global saved_audio_output, saved_volume
    global recording_proc, recording_file, recording_start_time

    if recording_proc:
        recording_proc.send_signal(signal.SIGINT)
        try:
            recording_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            recording_proc.kill()
        recording_proc = None
        recording_file = None
        recording_start_time = None

    if ffmpeg_proc:
        try:
            ffmpeg_proc.terminate()
            ffmpeg_proc.wait(timeout=2)
        except Exception:
            ffmpeg_proc.kill()
        ffmpeg_proc = None

    if gain_bridge_proc:
        try:
            gain_bridge_proc.terminate()
            gain_bridge_proc.wait(timeout=2)
        except Exception:
            gain_bridge_proc.kill()
        gain_bridge_proc = None

    if passthrough_resample_proc:
        try:
            passthrough_resample_proc.terminate()
            passthrough_resample_proc.wait(timeout=2)
        except Exception:
            passthrough_resample_proc.kill()
        passthrough_resample_proc = None

    if passthrough_quad_proc:
        try:
            passthrough_quad_proc.terminate()
            passthrough_quad_proc.wait(timeout=2)
        except Exception:
            passthrough_quad_proc.kill()
        passthrough_quad_proc = None

    if driver_proc:
        driver_proc.send_signal(signal.SIGINT)
        try:
            driver_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            driver_proc.kill()
        driver_proc = None

    if saved_audio_output:
        set_audio_output(saved_audio_output)
        saved_audio_output = None

    if saved_volume is not None:
        set_output_volume(saved_volume)
        saved_volume = None

    stream_start_time = None
    stream_mode = None
    stream_config = {}
    LOG.info("stop: ok")
    return {"status": "stopped"}


@app.post("/api/test-tone")
async def test_tone_endpoint(config: dict = {}):
    global driver_proc, stream_start_time, stream_config, stream_mode

    if driver_proc and driver_proc.poll() is None:
        await stop()

    if not build_driver():
        return JSONResponse({"error": "Build failed"}, 500)

    vol = float(config.get("volume", 0.1))
    ch = USB_PCM_CHANNELS
    rate = int(config.get("sample_rate", 48000))
    if rate not in (44100, 48000, 88200, 96000):
        rate = 48000

    stream_config = {"channels": ch, "sample_rate": rate, "volume": vol}

    driver_proc = subprocess.Popen(
        [C_BINARY, "--rate", str(rate), "--test-tone", str(vol), "--status-file", "/tmp/tascam_status.json"],
        stderr=subprocess.PIPE,
    )
    stream_start_time = time.time()
    stream_mode = "tone"
    LOG.info("test_tone: vol=%.4f ch=%d rate=%d Hz", vol, ch, rate)
    return {"status": "test_tone", "volume": vol}


@app.post("/api/chime")
async def chime_endpoint(config: dict = {}):
    global driver_proc, stream_start_time, stream_config, stream_mode

    if driver_proc and driver_proc.poll() is None:
        await stop()

    if not build_driver():
        return JSONResponse({"error": "Build failed"}, 500)

    vol = float(config.get("volume", 0.1))
    ch = USB_PCM_CHANNELS
    rate = int(config.get("sample_rate", 48000))
    if rate not in (44100, 48000, 88200, 96000):
        rate = 48000

    stream_config = {"channels": ch, "sample_rate": rate, "volume": vol}

    driver_proc = subprocess.Popen(
        [C_BINARY, "--rate", str(rate), "--chime", str(vol), "--status-file", "/tmp/tascam_status.json"],
        stderr=subprocess.PIPE,
    )
    stream_start_time = time.time()
    stream_mode = "tone"
    LOG.info("chime: vol=%.4f ch=%d rate=%d Hz", vol, ch, rate)
    return {"status": "chime", "volume": vol}


@app.post("/api/passthrough")
async def passthrough_start(config: dict = {}):
    global driver_proc, ffmpeg_proc, passthrough_resample_proc, gain_bridge_proc
    global passthrough_quad_proc
    global stream_start_time, stream_config, stream_mode, saved_audio_output, saved_volume

    if driver_proc and driver_proc.poll() is None:
        await stop()

    if not build_driver():
        return JSONResponse({"error": "Build failed"}, 500)

    gain = float(config.get("volume", 0.01))

    cb_req = (config.get("capture_backend") or "").strip().lower()
    if cb_req == "avfoundation":
        use_avfoundation = True
    elif cb_req in ("portaudio", "sounddevice", "sd", ""):
        use_avfoundation = (
            os.environ.get("TASCAM_PASSTHROUGH_CAPTURE", "").lower() == "avfoundation"
        )
    else:
        use_avfoundation = False

    mov = config.get("macos_output_volume")
    if mov is not None:
        try:
            macos_bh_vol = max(0, min(100, int(mov)))
        except (TypeError, ValueError):
            macos_bh_vol = _passthrough_macos_output_volume()
    else:
        macos_bh_vol = _passthrough_macos_output_volume()

    try:
        routing_delay = float(
            config.get("routing_delay_sec", ROUTING_DELAY_FALLBACK_SEC)
        )
    except (TypeError, ValueError):
        routing_delay = ROUTING_DELAY_FALLBACK_SEC
    routing_delay = max(0.0, min(2.0, routing_delay))

    try:
        out_sr = int(config.get("sample_rate", DEVICE_PCM_RATE))
    except (TypeError, ValueError):
        out_sr = DEVICE_PCM_RATE
    if out_sr not in (44100, 48000):
        out_sr = DEVICE_PCM_RATE

    force_sr_raw = config.get("force_capture_sample_rate", config.get("force_capture_sr"))
    capture_sr_forced = False
    if force_sr_raw is not None and str(force_sr_raw).strip() != "":
        try:
            f = int(force_sr_raw)
            if 8000 <= f <= 192000:
                capture_sr_forced = True
                resolved_capture_sr = f
            else:
                resolved_capture_sr = None
        except (TypeError, ValueError):
            resolved_capture_sr = None
    else:
        resolved_capture_sr = None

    saved_audio_output = get_current_audio_output()
    saved_volume = get_output_volume()
    set_audio_output(virtual_audio_device_name())
    set_output_volume(macos_bh_vol)

    time.sleep(routing_delay)
    if capture_sr_forced:
        capture_sr = resolved_capture_sr
    else:
        capture_sr = blackhole_input_sample_rate()

    usb_ch = USB_PCM_CHANNELS
    passthrough_quad_proc = None

    stream_config = {
        "channels": usb_ch,
        "sample_rate": out_sr,
        "capture_sample_rate": capture_sr,
        "capture_sr_forced": capture_sr_forced,
        "volume": gain,
        "macos_output_volume": macos_bh_vol,
        "routing_delay_sec": routing_delay,
        "capture_backend_request": cb_req or "env_default",
        "virtual_audio_device": virtual_audio_device_name(),
    }
    write_live_gain(gain)

    tascam_log = open(os.path.join(SCRIPT_DIR, "tascam.log"), "w")
    capture_log = open(os.path.join(SCRIPT_DIR, "capture.log"), "w")
    capture_log.write(
        f"=== passthrough start {time.strftime('%Y-%m-%dT%H:%M:%S')} "
        f"capture_sr={capture_sr} usb_pcm={out_sr}Hz ch={usb_ch} ===\n"
    )
    _write_passthrough_quality_preamble(capture_log)
    capture_log.flush()
    tascam_log.write(
        f"=== tascam_usb start {time.strftime('%Y-%m-%dT%H:%M:%S')} "
        f"{usb_ch}ch {out_sr}Hz ===\n"
    )
    tascam_log.flush()

    cap_env = os.environ.copy()
    cap_env["CAPTURE_GAIN"] = str(gain)
    cap_env["CAPTURE_GAIN_FILE"] = LIVE_GAIN_FILE
    cap_env["PASSTHROUGH_SR"] = str(capture_sr)
    cap_env.setdefault(
        "CAPTURE_QUEUE_BLOCKS",
        default_capture_queue_blocks_str(),
    )

    cbs = config.get(
        "capture_blocksize",
        API_PASSTHROUGH_DEFAULTS.get("capture_blocksize"),
    )
    if cbs is not None and str(cbs).strip() != "":
        try:
            ci = int(cbs)
            if 32 <= ci <= 8192:
                cap_env["CAPTURE_BLOCKSIZE"] = str(ci)
                stream_config["capture_blocksize"] = ci
        except (TypeError, ValueError):
            pass

    apply_tascam_capture_env_aliases(cap_env)
    # Default off: heartbeats append to capture.log and can stall the capture process.
    cap_env.setdefault("CAPTURE_HEARTBEAT_SEC", "0")

    passthrough_resample_proc = None
    gain_bridge_proc = None
    resampler_label = None
    capture_backend = "portaudio"

    if use_avfoundation:
        capture_backend = "avfoundation"
        bh_idx = _avfoundation_audio_index()
        # Do NOT run aresample when already at 48 kHz — forced same-rate resample sounds robotic / pitch-warped.
        avff_cmd = [
            FFMPEG,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "avfoundation",
            "-thread_queue_size",
            "512",
            "-i",
            f":{bh_idx}",
            "-vn",
        ]
        if capture_sr != out_sr:
            af_filter, resampler_label = _ffmpeg_aresample_filter_for_input_rate(
                capture_sr, out_sr
            )
            avff_cmd.extend(["-af", af_filter])
        else:
            resampler_label = "none"
            af_filter = None
        avff_cmd.extend(
            [
                "-f",
                "f32le",
                "-ac",
                "2",
                "-ar",
                str(out_sr),
                "-",
            ]
        )
        capture_log.write(
            f"[passthrough] capture_backend=avfoundation device_index=:{bh_idx} "
            f"input_nominal_sr={capture_sr} -> {out_sr} Hz "
            f"resampler={resampler_label} af={af_filter!r}\n"
        )
        capture_log.flush()
        ffmpeg_proc = subprocess.Popen(
            avff_cmd,
            stdout=subprocess.PIPE,
            stderr=capture_log,
        )
        gain_bridge_script = os.path.join(SCRIPT_DIR, "gain_bridge_s24.py")
        gain_bridge_proc = subprocess.Popen(
            [sys.executable, gain_bridge_script],
            stdin=ffmpeg_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=cap_env,
        )
        ffmpeg_proc.stdout.close()
        tascam_stdin = gain_bridge_proc.stdout
    else:
        capture_script = os.path.join(SCRIPT_DIR, "capture_blackhole.py")
        capture_log.write(
            f"[passthrough] capture_backend=portaudio input_sr={capture_sr} "
            f"-> tascam {out_sr} Hz\n"
        )
        capture_log.flush()
        ffmpeg_proc = subprocess.Popen(
            [sys.executable, capture_script],
            stdout=subprocess.PIPE,
            stderr=capture_log,
            env=cap_env,
        )

        if capture_sr != out_sr:
            af_filter, resampler_label = _ffmpeg_aresample_filter_for_input_rate(
                capture_sr, out_sr
            )
            resample_log_path = os.path.join(SCRIPT_DIR, "passthrough_resample.log")
            with open(resample_log_path, "w") as resample_log:
                resample_log.write(
                    f"passthrough resample: {capture_sr} -> {out_sr} Hz "
                    f"(engine={resampler_label})\n"
                )
                resample_log.flush()
                passthrough_resample_proc = subprocess.Popen(
                    [
                        FFMPEG,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "s24le",
                        "-ac",
                        "2",
                        "-ar",
                        str(capture_sr),
                        "-i",
                        "pipe:0",
                        "-af",
                        af_filter,
                        "-f",
                        "s24le",
                        "-ac",
                        "2",
                        "-ar",
                        str(out_sr),
                        "pipe:1",
                    ],
                    stdin=ffmpeg_proc.stdout,
                    stdout=subprocess.PIPE,
                    stderr=resample_log,
                )
            ffmpeg_proc.stdout.close()
            tascam_stdin = passthrough_resample_proc.stdout
        else:
            resampler_label = "none"
            tascam_stdin = ffmpeg_proc.stdout

    LOG.info(
        "passthrough: backend=%s capture_sr=%d usb_out=%d Hz usb_ch=%d resampler=%s gain=%.4f "
        "macos_vol=%d delay=%.2fs cap_forced=%s blocksize=%s",
        capture_backend,
        capture_sr,
        out_sr,
        usb_ch,
        resampler_label,
        gain,
        macos_bh_vol,
        routing_delay,
        capture_sr_forced,
        stream_config.get("capture_blocksize", "—"),
    )

    driver_argv = [
        C_BINARY,
        "--rate", str(out_sr),
        "--status-file", "/tmp/tascam_status.json",
    ]
    tascam_feed = tascam_stdin
    if usb_ch == 4:
        driver_argv.append("--stereo-stdin")
        capture_log.write(
            "[passthrough] stereo S24 → quad L,R,L,R in tascam_usb (--stereo-stdin; no Python hop)\n"
        )
        capture_log.flush()

    driver_proc = subprocess.Popen(
        driver_argv,
        stdin=tascam_feed,
        stderr=tascam_log,
    )
    tascam_feed.close()
    try:
        capture_log.flush()
    except Exception:
        pass

    await asyncio.sleep(POST_START_HEALTH_WAIT_SEC)
    fail_msgs = []
    if ffmpeg_proc and ffmpeg_proc.poll() is not None:
        fail_msgs.append("capture process exited early")
    if gain_bridge_proc and gain_bridge_proc.poll() is not None:
        fail_msgs.append("gain_bridge exited early")
    if passthrough_resample_proc and passthrough_resample_proc.poll() is not None:
        fail_msgs.append("resample ffmpeg exited early")
    if passthrough_quad_proc and passthrough_quad_proc.poll() is not None:
        fail_msgs.append("stereo_to_quad exited early")
    if driver_proc.poll() is not None:
        fail_msgs.append("tascam_usb exited early")

    if fail_msgs:
        try:
            tascam_log.flush()
        except Exception:
            pass
        try:
            with open(os.path.join(SCRIPT_DIR, "tascam.log")) as tf:
                tascam_tail = tf.read()[-2500:]
        except OSError:
            tascam_tail = ""
        try:
            with open(os.path.join(SCRIPT_DIR, "capture.log")) as cf:
                cap_tail = cf.read()[-2500:]
        except OSError:
            cap_tail = ""
        await stop()
        LOG.warning("passthrough failed: %s", "; ".join(fail_msgs))
        return JSONResponse(
            {
                "error": "; ".join(fail_msgs),
                "tascam_log_tail": tascam_tail,
                "capture_log_tail": cap_tail,
            },
            status_code=503,
        )

    stream_start_time = time.time()
    stream_mode = "passthrough"
    LOG.info("passthrough: running (tascam.log / capture.log in %s)", SCRIPT_DIR)
    stream_config["capture_backend"] = capture_backend
    stream_config["resampler"] = resampler_label
    return {
        "status": "passthrough",
        "audio_source": virtual_audio_device_name(),
        "previous_output": saved_audio_output,
        "capture_sample_rate": capture_sr,
        "capture_sr_forced": capture_sr_forced,
        "device_pcm_rate": out_sr,
        "usb_channels": usb_ch,
        "resampler": resampler_label,
        "capture_backend": capture_backend,
        "macos_output_volume": macos_bh_vol,
        "routing_delay_sec": routing_delay,
        "capture_blocksize": stream_config.get("capture_blocksize"),
        "capture_backend_request": stream_config.get("capture_backend_request"),
    }


async def _ws_try_send_json(ws: WebSocket, data: dict) -> bool:
    try:
        await ws.send_json(data)
        return True
    except WebSocketDisconnect:
        return False
    except RuntimeError:
        # Uvicorn: send after websocket.close / disconnected client
        return False


@app.websocket("/ws/stats")
async def ws_stats(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    cli = ws.scope.get("client")
    peer = f"{cli[0]}:{cli[1]}" if cli else "?"
    LOG.debug("ws /ws/stats connected %s (n=%d)", peer, len(ws_clients))
    prev = None
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_json(), timeout=0.5)
            except asyncio.TimeoutError:
                raw = None
            except WebSocketDisconnect:
                break
            except Exception:
                raw = None
            if isinstance(raw, dict) and raw.get("cmd") == "set_volume":
                try:
                    vol = max(0.0, min(1.0, float(raw.get("volume", 0.01))))
                    if stream_mode == "passthrough":
                        write_live_gain(vol)
                        LOG.debug("ws set_volume gain=%.4f", vol)
                    if not await _ws_try_send_json(ws, {"ack": "set_volume", "volume": vol}):
                        break
                except (TypeError, ValueError):
                    pass
            cur = get_stats()
            if cur != prev:
                if not await _ws_try_send_json(ws, cur):
                    break
                prev = cur
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)
        LOG.debug("ws /ws/stats closed (n=%d)", len(ws_clients))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8420)
