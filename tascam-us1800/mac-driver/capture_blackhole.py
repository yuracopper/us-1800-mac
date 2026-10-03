#!/usr/bin/env python3
"""Capture audio from a CoreAudio virtual input (default BlackHole 2ch) → stdout as S24_3LE.

Uses a callback + queue architecture to avoid blocking the audio thread
with pipe I/O, which is a common source of glitches.

Heartbeat lines go to a background thread so the consumer loop only does
queue.get + stdout.write (stderr flush from the main consumer was aligning
with ~10s / ~20s glitches when the parent pipe or disk I/O stalled briefly).

Set PASSTHROUGH_SR (from the API) to BlackHole's native rate so PortAudio does
not realtime-resample in the callback (avoid thin / doubled / metallic voices).

Env tuning: see passthrough_config.TUNABLE_ENV_VARS and GET /api/passthrough-config.
CAPTURE_GAIN_POLL_SEC: when CAPTURE_GAIN_FILE is set, min seconds between stat/read in the
PortAudio callback (default 0.033; use 0 to poll every callback).
"""
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone

import numpy as np
import sounddevice as sd

from capture_gain import float32_to_s24le_packed, gain_now
from passthrough_config import (
    CAPTURE_BLOCKSIZE_FALLBACK,
    CAPTURE_QUEUE_BLOCKS_FALLBACK,
    virtual_audio_device_name,
)
CHANNELS = 2
try:
    _raw_bs = int(
        os.environ.get("CAPTURE_BLOCKSIZE", str(CAPTURE_BLOCKSIZE_FALLBACK))
    )
except ValueError:
    _raw_bs = CAPTURE_BLOCKSIZE_FALLBACK
_rounded = (_raw_bs // 32) * 32
BLOCKSIZE = max(32, min(8192, _rounded if _rounded else CAPTURE_BLOCKSIZE_FALLBACK))
# Balance lip-sync vs glitches; lower = faster but harsher on CPU/scheduling.
try:
    QUEUE_MAX = max(
        3,
        min(
            128,
            int(
                os.environ.get(
                    "CAPTURE_QUEUE_BLOCKS", str(CAPTURE_QUEUE_BLOCKS_FALLBACK)
                )
            ),
        ),
    )
except ValueError:
    QUEUE_MAX = CAPTURE_QUEUE_BLOCKS_FALLBACK


def _drop_oldest_on_full():
    v = (os.environ.get("CAPTURE_QUEUE_DROP_OLDEST", "1") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _resolve_input_latency(dev_info):
    """Seconds (float) or 'high' for PortAudio InputStream latency=….

    When CAPTURE_LATENCY_AUTO is not '0', use the device's default_low_input_latency
    (BlackHole/CoreAudio truth). Override with CAPTURE_LATENCY_SEC=…

    Avoid CAPTURE_LATENCY=high for this callback+queue design: CoreAudio's large buffer
    often delivers bursty callbacks; a small queue then hits queue_full_drop and the
    USB side underruns (robotic audio). Prefer auto/low latency and deeper queue.
    """
    raw = (os.environ.get("CAPTURE_LATENCY_SEC") or "").strip()
    if raw:
        try:
            sec = float(raw)
            if 0.005 <= sec <= 0.5:
                return sec
        except ValueError:
            pass
    v = (os.environ.get("CAPTURE_LATENCY") or "low").strip().lower()
    if v == "high":
        return "high"
    auto = (os.environ.get("CAPTURE_LATENCY_AUTO", "1") or "1").strip().lower()
    if auto not in ("0", "false", "no", "off"):
        try:
            low = float(dev_info.get("default_low_input_latency") or 0.0)
        except (TypeError, ValueError):
            low = 0.0
        if low > 0:
            floor = float(os.environ.get("CAPTURE_LATENCY_FLOOR_SEC") or "0.006")
            cap = float(os.environ.get("CAPTURE_LATENCY_CAP_SEC") or "0.08")
            sec = max(floor, min(cap, low))
            return sec
    return float(os.environ.get("CAPTURE_LATENCY_DEFAULT_SEC", "0.021"))


def _glitch_printer(stderr):
    """Wall-clock lines for post-mortem (search capture.log for [glitch])."""
    last = 0.0

    def emit(msg: str, *, min_s: float = 0.35):
        nonlocal last
        now = time.monotonic()
        if now - last < min_s:
            return
        last = now
        wall = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"[capture][glitch] {wall} {msg}", file=stderr, flush=True)

    return emit


def main():
    stderr = sys.stderr
    device = virtual_audio_device_name()
    rate = int(os.environ.get("PASSTHROUGH_SR", "48000"))
    use_file = bool(os.environ.get("CAPTURE_GAIN_FILE"))

    g0 = gain_now(use_file)
    print(
        f"[capture] device_name={device!r} PASSTHROUGH_SR={rate} initial gain={g0} live_file={use_file} "
        f"CAPTURE_GAIN={os.environ.get('CAPTURE_GAIN', '')!r}",
        file=stderr,
    )

    dev_info = sd.query_devices(device, kind="input")
    default_sr = dev_info["default_samplerate"]
    print(f"[capture] device: {dev_info['name']}", file=stderr)
    print(f"[capture] default sr: {default_sr}", file=stderr)
    print(f"[capture] max input ch: {dev_info['max_input_channels']}", file=stderr)
    lat = _resolve_input_latency(dev_info)
    if lat == "high":
        print(
            "[capture] warning: CAPTURE_LATENCY=high often causes queue_full_drop with "
            "BlackHole (bursty I/O vs small chunk queue). Remove it or raise "
            "CAPTURE_QUEUE_BLOCKS; expect robotic USB underruns if you see [glitch] drops.",
            file=stderr,
        )
    drop_old = _drop_oldest_on_full()
    print(f"[capture] capture sr: {rate}", file=stderr)
    print(
        f"[capture] blocksize={BLOCKSIZE} queue_max={QUEUE_MAX} "
        f"latency={lat!r} (device low_in={dev_info.get('default_low_input_latency')}, "
        f"high_in={dev_info.get('default_high_input_latency')}, CAPTURE_LATENCY_AUTO="
        f"{(os.environ.get('CAPTURE_LATENCY_AUTO') or '1')!r}, "
        f"CAPTURE_QUEUE_DROP_OLDEST={drop_old!r})",
        file=stderr,
    )

    if int(float(default_sr)) != rate:
        print(
            f"[capture] warning: device default_sr={default_sr} but capture sr={rate}",
            file=stderr,
        )

    audio_q = queue.Queue(maxsize=QUEUE_MAX)
    stats = {"frames": 0, "drops": 0, "overflows": 0, "t0": time.monotonic()}
    glitch = _glitch_printer(stderr)
    # Live gain file: avoid stat()+open every callback (~150/s); set CAPTURE_GAIN_POLL_SEC=0 to always poll.
    try:
        _gpoll = float((os.environ.get("CAPTURE_GAIN_POLL_SEC") or "0.033").strip())
    except ValueError:
        _gpoll = 0.033
    gain_rt = {"g": g0, "t": time.monotonic()}

    def callback(indata, frames, time_info, status):
        if status.input_overflow:
            stats["overflows"] += 1
            glitch(
                f"portaudio_input_overflow total={stats['overflows']} "
                f"(CPU load / scheduling or device buffer — see BlackHole & CAPTURE_*)"
            )
        elif status:
            stats["pa_status_other"] = stats.get("pa_status_other", 0) + 1
            glitch(
                f"portaudio_status {status!r} count={stats['pa_status_other']} "
                f"(robotic/metallic — try CAPTURE_QUEUE_BLOCKS↑, blocksize tweak, not CAPTURE_LATENCY=high)"
            )
        stats["frames"] += frames
        if use_file and _gpoll > 0:
            now_g = time.monotonic()
            if now_g - gain_rt["t"] >= _gpoll:
                gain_rt["t"] = now_g
                gain_rt["g"] = gain_now(use_file)
            g = gain_rt["g"]
        else:
            g = gain_now(use_file)
        scaled_audio = np.ascontiguousarray(indata, dtype=np.float32) * g
        chunk = float32_to_s24le_packed(scaled_audio)
        try:
            audio_q.put_nowait(chunk)
        except queue.Full:
            stats["drops"] += 1
            glitch(
                f"queue_full_drop total={stats['drops']} q_max={QUEUE_MAX} "
                f"(writer blocked / bursty capture — unset CAPTURE_LATENCY=high, raise CAPTURE_QUEUE_BLOCKS)"
            )
            if drop_old:
                try:
                    audio_q.get_nowait()
                    audio_q.put_nowait(chunk)
                except queue.Empty:
                    pass
                except queue.Full:
                    pass

    stream = sd.InputStream(
        device=device,
        samplerate=rate,
        channels=CHANNELS,
        blocksize=BLOCKSIZE,
        dtype="float32",
        latency=lat,
        callback=callback,
    )
    stream.start()
    print(f"[capture] actual sr: {stream.samplerate}", file=stderr)
    print("[capture] streaming...", file=stderr)

    stdout = os.fdopen(sys.stdout.fileno(), "wb", 0)  # unbuffered
    # Heartbeat interval (set CAPTURE_HEARTBEAT_SEC=0 to disable). Default off: matches API
    # (stderr to capture.log can block; old default 10s matched ~10–20s glitch reports).
    tryHB = float(os.environ.get("CAPTURE_HEARTBEAT_SEC", "0"))
    hb_every = 0.0 if tryHB <= 0 else max(5.0, tryHB)
    hb_stop = threading.Event()

    def _heartbeat_worker():
        while not hb_stop.wait(timeout=hb_every):
            elapsed = time.monotonic() - stats["t0"]
            if elapsed < 1.0:
                continue
            rate_pct = stats["frames"] / (rate * elapsed) * 100
            oth = stats.get("pa_status_other", 0)
            oth_s = f" pa_other={oth}" if oth else ""
            line = (
                f"[capture][heartbeat] t={elapsed:.0f}s frames={rate_pct:.1f}% "
                f"drops={stats['drops']} overflows={stats['overflows']}{oth_s} "
                f"qsz={audio_q.qsize()} gain={gain_now(use_file)}\n"
            )
            try:
                os.write(2, line.encode("utf-8", errors="replace"))
            except OSError:
                pass

    if hb_every > 0:
        threading.Thread(target=_heartbeat_worker, name="capture_hb", daemon=True).start()

    try:
        while True:
            try:
                data = audio_q.get(timeout=1.0)
            except queue.Empty:
                continue
            stdout.write(data)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        hb_stop.set()
        stream.stop()
        stream.close()
        elapsed = time.monotonic() - stats["t0"]
        oth = stats.get("pa_status_other", 0)
        oth_s = f" pa_status_other={oth}" if oth else ""
        print(
            f"[capture][session end] {stats['frames']} frames in {elapsed:.1f}s "
            f"drops={stats['drops']} overflows={stats['overflows']}{oth_s}",
            file=stderr,
            flush=True,
        )


if __name__ == "__main__":
    main()
