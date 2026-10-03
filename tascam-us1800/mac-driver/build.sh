#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
echo "=== Building TASCAM US-1800 Low-Latency Driver Suite ==="

rm -f "$DIR/tascam_live_engine" "$DIR/monitor" 2>/dev/null || true

# 1. Compile Live USB Audio Engine
echo "[1/3] Compiling tascam_live_engine..."
DEVELOPER_DIR=/Library/Developer/CommandLineTools clang -O3 -Wall -Wextra -Wno-format-extra-args \
  -isysroot /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk -arch arm64 \
  -I"$DIR" -framework IOKit -framework CoreFoundation \
  -o "$DIR/tascam_live_engine" "$DIR/tascam_live_engine.c"
chmod 755 "$DIR/tascam_live_engine"

# 2. Compile CoreAudio HAL Plugin Bundle
echo "[2/3] Compiling TASCAM_US1800.driver (CoreAudio HAL Plugin)..."
mkdir -p "$DIR/TASCAM_US1800.driver/Contents/MacOS"
rm -f "$DIR/TASCAM_US1800.driver/Contents/MacOS/TASCAM_US1800" 2>/dev/null || true
DEVELOPER_DIR=/Library/Developer/CommandLineTools clang -bundle -O3 -Wno-format-extra-args \
  -isysroot /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk -arch arm64 \
  -framework CoreAudio -framework CoreFoundation -framework Accelerate \
  -I"$DIR" \
  -o "$DIR/TASCAM_US1800.driver/Contents/MacOS/TASCAM_US1800" \
  "$DIR/tascam_hal_plugin.c"
chmod 755 "$DIR/TASCAM_US1800.driver/Contents/MacOS/TASCAM_US1800"

# Ensure Info.plist is present
if [ ! -f "$DIR/TASCAM_US1800.driver/Contents/Info.plist" ]; then
    echo "ERROR: Info.plist missing from TASCAM_US1800.driver!" >&2
    exit 1
fi

codesign -f -s - "$DIR/TASCAM_US1800.driver" 2>/dev/null || true

# 3. Compile Diagnostic Monitor
echo "[3/3] Compiling diagnostic monitor..."
DEVELOPER_DIR=/Library/Developer/CommandLineTools clang -O2 -Wno-format-extra-args \
  -isysroot /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk -arch arm64 \
  -I"$DIR" \
  -o "$DIR/monitor" "$DIR/monitor.c"
chmod 755 "$DIR/monitor"

echo "=== Build Complete Successfully! ==="
