#!/usr/bin/env python3
"""stdin: float32 stereo PCM (any rate ffmpeg outputs); stdout: S24_3LE packed.

Used after FFmpeg AVFoundation capture so live gain (file + UI) still works
without PortAudio/sounddevice in the capture path.
"""
import os
import sys

import numpy as np

from capture_gain import float32_to_s24le_packed, gain_now

READ_BYTES = 65536


def main():
    use_file = bool(os.environ.get("CAPTURE_GAIN_FILE"))
    stdin = sys.stdin.buffer
    stdout = os.fdopen(sys.stdout.fileno(), "wb", 0)
    buf = bytearray()
    try:
        while True:
            chunk = stdin.read(READ_BYTES)
            if not chunk:
                break
            buf.extend(chunk)
            nframe_bytes = (len(buf) // 8) * 8
            if nframe_bytes == 0:
                continue
            raw = bytes(buf[:nframe_bytes])
            buf = buf[nframe_bytes:]
            arr = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)
            g = gain_now(use_file)
            if g != 1.0:
                arr = arr * g
            stdout.write(float32_to_s24le_packed(arr))
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()
