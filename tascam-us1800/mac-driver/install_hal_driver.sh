#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$DIR/TASCAM_US1800.driver" ]; then
    DRIVER_SRC="$DIR/TASCAM_US1800.driver"
elif [ -d "/Users/yura/Desktop/tascam-us1800/mac-driver/TASCAM_US1800.driver" ]; then
    DRIVER_SRC="/Users/yura/Desktop/tascam-us1800/mac-driver/TASCAM_US1800.driver"
elif [ -d "/Users/yura/.gemini/antigravity/scratch/tascam-us1800/mac-driver/TASCAM_US1800.driver" ]; then
    DRIVER_SRC="/Users/yura/.gemini/antigravity/scratch/tascam-us1800/mac-driver/TASCAM_US1800.driver"
fi
HAL_DIR="/Library/Audio/Plug-Ins/HAL"
TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || whoami)}"
USER_HOME=$(eval echo "~$TARGET_USER")
APP_DIR="$USER_HOME/Desktop/TASCAM US-1800.app"

echo "=========================================================="
echo " Installing TASCAM US-1800 CoreAudio HAL Driver"
echo "      Apple Silicon M1-M4 (Ultra-Low Latency Engine)      "
echo "=========================================================="

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "Administrator privileges are required."
    exec sudo "$0" "$@"
fi

# 1. Build latest driver & engine suite
"$DIR/build.sh"

# 2. Stop running engine
launchctl bootout system/com.tascam.us1800.live 2>/dev/null || true
launchctl unload /Library/LaunchDaemons/com.tascam.us1800.live.plist 2>/dev/null || true
killall -9 tascam_live_engine 2>/dev/null || true
pkill -9 -f tascam_live_engine 2>/dev/null || true
sleep 1

# 3. Clean old shared memory and set mode to Ultra-Low (0)
rm -f /var/tmp/tascam* /tmp/tascam* 2>/dev/null || true
echo "0" > /var/tmp/tascam_mode.conf
chmod 666 /var/tmp/tascam_mode.conf 2>/dev/null || true

# 4. Install HAL plugin bundle
echo "[4/7] Installing TASCAM_US1800.driver to $HAL_DIR..."
mkdir -p "$HAL_DIR"
rm -rf "$HAL_DIR/TASCAM_US1800.driver"
cp -R "$DRIVER_SRC" "$HAL_DIR/"
chown -R root:wheel "$HAL_DIR/TASCAM_US1800.driver"
chmod -R 755 "$HAL_DIR/TASCAM_US1800.driver"

# 5. Install live engine binary
echo "[5/7] Installing tascam_live_engine to /usr/local/bin..."
mkdir -p /usr/local/bin
cp "$DIR/tascam_live_engine" /usr/local/bin/tascam_live_engine
chown root:wheel /usr/local/bin/tascam_live_engine
chmod 755 /usr/local/bin/tascam_live_engine

# Update Desktop App resources
if [ -d "$APP_DIR/Contents" ]; then
    mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
    cp "$DIR/tascam_live_engine" "$APP_DIR/Contents/Resources/tascam_live_engine" 2>/dev/null || true
    cp "$DIR/tascam_gui.py" "$APP_DIR/Contents/Resources/tascam_gui.py" 2>/dev/null || true
    cp "$DIR/tascam_console_native" "$APP_DIR/Contents/MacOS/tascam_console_native" 2>/dev/null || true
    chmod 755 "$APP_DIR/Contents/MacOS/tascam_console_native" 2>/dev/null || true
    chown -R "$TARGET_USER":staff "$APP_DIR" 2>/dev/null || true
fi

# 6. Configure LaunchDaemon
echo "[6/7] Configuring LaunchDaemon auto-start service..."
cat << 'EOF' > /Library/LaunchDaemons/com.tascam.us1800.live.plist
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tascam.us1800.live</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/tascam_live_engine</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/var/log/tascam_live.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/tascam_live_err.log</string>
</dict>
</plist>
EOF

chown root:wheel /Library/LaunchDaemons/com.tascam.us1800.live.plist
chmod 644 /Library/LaunchDaemons/com.tascam.us1800.live.plist

# 7. Restart CoreAudio and launch engine
echo "[7/7] Restarting CoreAudio and launching engine..."
killall coreaudiod 2>/dev/null || true
sleep 1

launchctl bootstrap system /Library/LaunchDaemons/com.tascam.us1800.live.plist 2>/dev/null || \
launchctl load -w /Library/LaunchDaemons/com.tascam.us1800.live.plist 2>/dev/null || true
launchctl kickstart -k system/com.tascam.us1800.live 2>/dev/null || true

sleep 1

chown -R "$TARGET_USER":staff "$DIR" 2>/dev/null || true

echo ""
echo "=========================================================="
echo "    [✓] TASCAM US-1800 LOW-LATENCY DRIVER INSTALLED!      "
echo "  16 Inputs / 4 Outputs active with Ultra-Low Latency     "
echo "  Device: TASCAM US-1800 (USB Audio Device)               "
echo "=========================================================="
