#!/usr/bin/env python3
"""stdin: packed S24_3LE stereo; stdout: S24_3LE quad L,R,L,R (duplicate pairs).

API passthrough uses tascam_usb --stereo-stdin (expansion in C) instead of this script.
Keep for manual shell pipelines:  capture_blackhole.py | this | tascam_usb 4 48000

Chunk size: TASCAM_STEREO_QUAD_CHUNK (bytes, multiple of 6; default 12288).
"""
import os
import sys

try:
    _raw = int(os.environ.get("TASCAM_STEREO_QUAD_CHUNK", "12288"))
except ValueError:
    _raw = 12288
_CHUNK = max(1536, min(98304, (_raw // 6) * 6))


def main():
    stdin = sys.stdin.buffer
    stdout = open(sys.stdout.fileno(), "wb", buffering=0)
    try:
        while True:
            raw = stdin.read(_CHUNK)
            if not raw:
                break
            n = len(raw) // 6
            if n == 0:
                continue
            raw = memoryview(raw)[: n * 6]
            out = bytearray(n * 12)
            for i in range(n):
                o = i * 6
                j = i * 12
                out[j : j + 6] = raw[o : o + 6]
                out[j + 6 : j + 12] = raw[o : o + 6]
            stdout.write(out)
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()
