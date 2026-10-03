"""Live passthrough gain: CAPTURE_GAIN and optional CAPTURE_GAIN_FILE (mtime-cached).

Shared by capture_blackhole (PortAudio) and gain_bridge_s24 (FFmpeg path).
"""
from __future__ import annotations

import os

import numpy as np

_gain_cache: dict = {"mtime_id": None, "val": 1.0}


def gain_env() -> float:
    try:
        return float(os.environ.get("CAPTURE_GAIN", "1.0"))
    except ValueError:
        return 1.0


def gain_now(use_file: bool) -> float:
    if not use_file:
        return gain_env()
    path = os.environ.get("CAPTURE_GAIN_FILE")
    if not path:
        return gain_env()
    try:
        st = os.stat(path)
        sid = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
        if _gain_cache["mtime_id"] != sid:
            with open(path) as f:
                _gain_cache["val"] = max(0.0, min(1.0, float(f.read().strip())))
            _gain_cache["mtime_id"] = sid
    except (OSError, ValueError):
        return gain_env()
    return float(_gain_cache["val"])


def float32_to_s24le_packed(data: np.ndarray) -> bytes:
    """float32 (any shape) → packed S24_3LE bytes."""
    flat = np.ascontiguousarray(data, dtype=np.float32).ravel()
    scaled = np.clip(flat * 8388607.0, -8388608.0, 8388607.0).astype(np.int32)
    out = np.empty(len(flat) * 3, dtype=np.uint8)
    out[0::3] = (scaled & 0xFF).astype(np.uint8)
    out[1::3] = ((scaled >> 8) & 0xFF).astype(np.uint8)
    out[2::3] = ((scaled >> 16) & 0xFF).astype(np.uint8)
    return out.tobytes()
