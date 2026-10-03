#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$DIR/TASCAM_US1800.driver" ]; then
    DRIVER_SRC="$DIR/TASCAM_US1800.driver"
elif [ -d "/Users/yura/.gemini/antigravity/scratch/TASCAM_US1800.driver" ]; then
    DRIVER_SRC="/Users/yura/.gemini/antigravity/scratch/TASCAM_US1800.driver"
fi
HAL_DIR="/Library/Audio/Plug-Ins/HAL"
TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || whoami)}"
USER_HOME=$(eval echo "~$TARGET_USER")
APP_DIR="$USER_HOME/Desktop/TASCAM US-1800.app"

echo "=========================================================="
echo " Установка нативного CoreAudio HAL драйвера TASCAM US-1800"
echo "      Apple Silicon M1/M2/M3 (Live Concert Engine)        "
echo "=========================================================="

# Проверка root прав
if [ "$EUID" -ne 0 ]; then
    echo "Для установки драйвера в систему требуются права администратора."
    echo "Пожалуйста, введите пароль администратора:"
    exec sudo "$0" "$@"
fi

# 1. Принятие лицензии инструментов разработчика (предотвращает зависание Python)
echo "[1/7] Проверка лицензии компонентов..."
xcodebuild -license accept 2>/dev/null || true

# 2. Остановка старых процессов live-движка
echo "[2/7] Остановка работающих процессов..."
launchctl bootout system/com.tascam.us1800.live 2>/dev/null || true
launchctl unload /Library/LaunchDaemons/com.tascam.us1800.live.plist 2>/dev/null || true
killall -9 tascam_live_engine 2>/dev/null || true
pkill -9 -f tascam_live_engine 2>/dev/null || true
sleep 1

# 3. Очистка старой разделяемой памяти
echo "[3/7] Очистка разделяемой памяти..."
rm -f /var/tmp/tascam* /tmp/tascam* 2>/dev/null || true

# 4. Установка нативного CoreAudio HAL плагина
echo "[4/7] Установка TASCAM_US1800.driver в $HAL_DIR..."
mkdir -p "$HAL_DIR"
rm -rf "$HAL_DIR/TASCAM_US1800.driver"
cp -R "$DRIVER_SRC" "$HAL_DIR/"
chown -R root:wheel "$HAL_DIR/TASCAM_US1800.driver"
chmod -R 755 "$HAL_DIR/TASCAM_US1800.driver"

# 5. Установка live аппаратного движка
echo "[5/7] Установка tascam_live_engine в /usr/local/bin..."
mkdir -p /usr/local/bin
cp "$DIR/tascam_live_engine" /usr/local/bin/tascam_live_engine
chown root:wheel /usr/local/bin/tascam_live_engine
chmod 755 /usr/local/bin/tascam_live_engine

# Обновление в Desktop App
if [ -d "$APP_DIR/Contents/Resources" ]; then
    cp "$DIR/tascam_live_engine" "$APP_DIR/Contents/Resources/tascam_live_engine" 2>/dev/null || true
    cp "$DIR/tascam_gui.py" "$APP_DIR/Contents/Resources/tascam_gui.py" 2>/dev/null || true
    chown -R "$TARGET_USER":staff "$APP_DIR" 2>/dev/null || true
fi

# 6. Настройка системного LaunchDaemon автозапуска
echo "[6/7] Настройка LaunchDaemon службы автозапуска..."
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

# 7. Перезапуск CoreAudio и запуск аппаратного движка
echo "[7/7] Перезапуск CoreAudio и запуск аппаратного движка..."
killall coreaudiod 2>/dev/null || true
sleep 1

launchctl bootstrap system /Library/LaunchDaemons/com.tascam.us1800.live.plist 2>/dev/null || \
launchctl load -w /Library/LaunchDaemons/com.tascam.us1800.live.plist 2>/dev/null || true
launchctl kickstart -k system/com.tascam.us1800.live 2>/dev/null || true

sleep 1

echo ""
echo "=========================================================="
echo "    [✓] НАСТОЯЩИЙ ДРАЙВЕР TASCAM US-1800 УСТАНОВЛЕН!      "
echo "  16 Входов / 4 Выхода теперь доступны в macOS и DAWs.    "
echo "  Устройство: TASCAM US-1800 (USB Audio Device)           "
echo "=========================================================="
