"""Passthrough defaults and TASCAM_* → child-process env aliases.

Single source of truth for tuning knobs shared by tascam_api, capture_blackhole,
and the web UI (via GET /api/passthrough-config).
"""
from __future__ import annotations

import os
from typing import Any

DEFAULT_VIRTUAL_AUDIO_DEVICE = "BlackHole 2ch"
DEVICE_PCM_HZ = 48000


def virtual_audio_device_name() -> str:
    """CoreAudio device for system output + capture (default ``BlackHole 2ch``).

    Set TASCAM_VIRTUAL_AUDIO_DEVICE to the exact name shown in Audio MIDI Setup,
    e.g. ``VB-Cable``, ``Loopback Audio``, or another 2ch+ virtual input.
    Alias: TASCAM_BLACKHOLE_NAME (legacy).
    """
    for key in ("TASCAM_VIRTUAL_AUDIO_DEVICE", "TASCAM_BLACKHOLE_NAME"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    return DEFAULT_VIRTUAL_AUDIO_DEVICE

# USB PCM out to tascam_usb (stereo duplicated to quad for US-1800).
USB_PCM_CHANNELS = 4

# POST /api/passthrough body defaults (UI merges volume on top; channels are fixed).
API_PASSTHROUGH_DEFAULTS: dict[str, Any] = {
    "capture_backend": "portaudio",
    "macos_output_volume": 100,
    # Wait after switching output to the virtual device before probing SR / starting capture (startup only).
    "routing_delay_sec": 0.03,
    "capture_blocksize": 320,
    "channels": USB_PCM_CHANNELS,
    "sample_rate": DEVICE_PCM_HZ,
}

ROUTING_DELAY_FALLBACK_SEC = float(API_PASSTHROUGH_DEFAULTS["routing_delay_sec"])

# When API does not set CAPTURE_BLOCKSIZE (standalone capture_blackhole).
CAPTURE_BLOCKSIZE_FALLBACK = 960
# Deeper queue absorbs brief stdout/pipe stalls without dropping chunks; too shallow +
# CAPTURE_LATENCY=high (bursty callbacks) caused mass queue_full_drop → USB underruns.
# Trade-off: more blocks → more cushioning for slow pipe writes; slightly higher latency.
CAPTURE_QUEUE_BLOCKS_FALLBACK = 36

# asyncio.sleep after spawning pipeline before health-check (not steady-state latency).
# API-only wait after spawning pipeline before checking children (does not pad the PCM stream).
POST_START_HEALTH_WAIT_SEC = 0.08

# If TASCAM_* is set in the API process environment, copy into capture child env.
TASCAM_TO_CAPTURE_ENV: tuple[tuple[str, str], ...] = (
    ("TASCAM_CAPTURE_QUEUE_BLOCKS", "CAPTURE_QUEUE_BLOCKS"),
    ("TASCAM_CAPTURE_LATENCY_AUTO", "CAPTURE_LATENCY_AUTO"),
    ("TASCAM_CAPTURE_LATENCY_SEC", "CAPTURE_LATENCY_SEC"),
    ("TASCAM_CAPTURE_LATENCY_FLOOR_SEC", "CAPTURE_LATENCY_FLOOR_SEC"),
    ("TASCAM_CAPTURE_LATENCY_CAP_SEC", "CAPTURE_LATENCY_CAP_SEC"),
    ("TASCAM_CAPTURE_LATENCY_DEFAULT_SEC", "CAPTURE_LATENCY_DEFAULT_SEC"),
    ("TASCAM_CAPTURE_QUEUE_DROP_OLDEST", "CAPTURE_QUEUE_DROP_OLDEST"),
    ("TASCAM_CAPTURE_BLOCKSIZE", "CAPTURE_BLOCKSIZE"),
    ("TASCAM_CAPTURE_HEARTBEAT_SEC", "CAPTURE_HEARTBEAT_SEC"),
)

# Reference for operators (also returned as JSON).
TUNABLE_ENV_VARS: list[dict[str, str]] = [
    {
        "env": "TASCAM_PASSTHROUGH_CAPTURE",
        "effect": "API process: portaudio (default) or avfoundation (FFmpeg).",
    },
    {
        "env": "TASCAM_VIRTUAL_AUDIO_DEVICE",
        "effect": "API + capture: CoreAudio device name for routing + input (default BlackHole 2ch). Try VB-Cable/Loopback if preferred.",
    },
    {
        "env": "TASCAM_BLACKHOLE_NAME",
        "effect": "Alias for TASCAM_VIRTUAL_AUDIO_DEVICE (legacy).",
    },
    {
        "env": "TASCAM_BLACKHOLE_OUTPUT_VOLUME",
        "effect": "API process: 0–100 macOS output level when switching to the virtual device.",
    },
    {
        "env": "TASCAM_CAPTURE_QUEUE_BLOCKS",
        "effect": f"Child: PortAudio chunk queue depth (default {CAPTURE_QUEUE_BLOCKS_FALLBACK}). Lower = less monitoring delay; raise 48–64 if capture.log shows queue_full_drop.",
    },
    {
        "env": "TASCAM_CAPTURE_QUEUE_DROP_OLDEST",
        "effect": "Child: 1 = on queue full discard oldest (default); 0 = drop new only.",
    },
    {
        "env": "CAPTURE_LATENCY",
        "effect": "capture_blackhole only: default 'low'. Do not set 'high' with bursty virtual devices (e.g. BlackHole) — raise CAPTURE_QUEUE_BLOCKS instead.",
    },
    {
        "env": "TASCAM_CAPTURE_LATENCY_AUTO",
        "effect": "Child: 1 = use device default_low_input_latency; 0 = use fixed default.",
    },
    {
        "env": "TASCAM_CAPTURE_LATENCY_SEC",
        "effect": "Child: overrides auto — fixed PortAudio input latency in seconds.",
    },
    {
        "env": "TASCAM_CAPTURE_LATENCY_FLOOR_SEC",
        "effect": "Child: minimum seconds when using auto (default 0.006).",
    },
    {
        "env": "TASCAM_CAPTURE_LATENCY_CAP_SEC",
        "effect": "Child: maximum seconds when using auto (default 0.08).",
    },
    {
        "env": "TASCAM_CAPTURE_LATENCY_DEFAULT_SEC",
        "effect": "Child: fixed latency when auto off (default 0.021).",
    },
    {
        "env": "TASCAM_CAPTURE_BLOCKSIZE",
        "effect": "Child: override block size (also set from API capture_blocksize).",
    },
    {
        "env": "TASCAM_STEREO_QUAD_CHUNK",
        "effect": "stereo_to_quad_s24.py: S24 chunk bytes (multiple of 6; default 12288).",
    },
    {
        "env": "TASCAM_CAPTURE_QUALITY_PREAMBLE",
        "effect": "API: 1 = write full quality checklist into capture.log at start; default short one-liner.",
    },
    {
        "env": "CAPTURE_HEARTBEAT_SEC",
        "effect": "capture_blackhole: stderr to capture.log interval (API default 0=off). Non-zero can correlate with periodic clicks when the log blocks.",
    },
    {
        "env": "TASCAM_CAPTURE_HEARTBEAT_SEC",
        "effect": "API alias: if set before start, copied to CAPTURE_HEARTBEAT_SEC for the capture child.",
    },
    {
        "env": "TASCAM_USB_RING_LOG_SEC",
        "effect": "tascam_usb: seconds between [ring] lines (default 0=off). Set 15+ only if debugging; file logging can glitch audio.",
    },
    {
        "env": "TASCAM_USB_PREBUFFER_KB",
        "effect": "tascam_usb: stdin ring fill (KB) before USB starts (default 10). Lower = less delay; raise to 14–16 if start underruns.",
    },
    {
        "env": "TASCAM_USB_TRIM_GRACE_SEC",
        "effect": "tascam_usb: seconds before startup_shed + latency trim (default 0.15).",
    },
    {
        "env": "TASCAM_USB_TRIM_SLACK_MS",
        "effect": "tascam_usb: jitter pad (ms) inside steady-state hi target before headroom (default 22; 12–80). Lower = tighter cap on ring latency.",
    },
    {
        "env": "TASCAM_USB_STARTUP_LIVE_MS",
        "effect": "tascam_usb: ms of 4ch PCM allowed above prebuffer after one-shot startup_shed (default 42). Lower = snappier first run, higher click risk.",
    },
    {
        "env": "TASCAM_USB_STEADY_TRIM",
        "effect": "tasm_usb: 1 (default) = steady-state latency_trim when ring > hi; 0 = disable (only startup_shed + rare panic shed). Try 0 if robotic bursts persist — bug may be trim/isoch, not virtual cable.",
    },
    {
        "env": "TASCAM_USB_STEADY_HEADROOM_MS",
        "effect": "tasm_usb: extra ms above prebuffer+slack before steady trim (default 55; 40–500). Lower = less delay; raise if latency_trim spam or underruns.",
    },
    {
        "env": "TASCAM_USB_PROACTIVE_RESYNC_SEC",
        "effect": "tasm_usb: proactive isoch resync interval in seconds (default 17; 0 = off). Up to 600.",
    },
    {
        "env": "TASCAM_USB_PROACTIVE_BLEND_FRAMES",
        "effect": "tasm_usb: S24 crossfade after proactive resync — smootherstep linear mix (default 4800 ~100 ms @ 48 k; 0 = off; up to 32768).",
    },
]


def default_capture_queue_blocks_str() -> str:
    return os.environ.get(
        "TASCAM_CAPTURE_QUEUE_BLOCKS", str(CAPTURE_QUEUE_BLOCKS_FALLBACK)
    )


def apply_tascam_capture_env_aliases(cap_env: dict) -> None:
    """Copy TASCAM_* overrides from this process into cap_env for capture children."""
    for tascam_key, cap_key in TASCAM_TO_CAPTURE_ENV:
        if tascam_key in os.environ:
            cap_env[cap_key] = os.environ[tascam_key]


def passthrough_config_response() -> dict[str, Any]:
    return {
        "defaults": dict(API_PASSTHROUGH_DEFAULTS),
        "device_pcm_rate_hz": DEVICE_PCM_HZ,
        "blackhole_name": virtual_audio_device_name(),
        "virtual_audio_device": virtual_audio_device_name(),
        "default_virtual_audio_device": DEFAULT_VIRTUAL_AUDIO_DEVICE,
        "post_start_health_wait_sec": POST_START_HEALTH_WAIT_SEC,
        "capture_script": {
            "blocksize_fallback": CAPTURE_BLOCKSIZE_FALLBACK,
            "queue_blocks_fallback": CAPTURE_QUEUE_BLOCKS_FALLBACK,
        },
        "tunable_env_vars": TUNABLE_ENV_VARS,
    }
