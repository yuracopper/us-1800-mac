#!/bin/bash
set -e

DIR="/Users/yura/.gemini/antigravity/scratch/tascam-us1800/mac-driver"

echo "=== Установка TASCAM US-1800 Driver & Manager ==="

# 1. Копирование бинарника драйвера
mkdir -p "$HOME/.local/bin"
cp "$DIR/tascam_usb" "$HOME/.local/bin/tascam_usb"
echo "[✓] Драйвер скопирован в $HOME/.local/bin/tascam_usb"

if [ -w /usr/local/bin ]; then
    cp "$DIR/tascam_usb" /usr/local/bin/tascam_usb
    echo "[✓] Драйвер скопирован в /usr/local/bin/tascam_usb"
fi

# 2. Копирование приложения на Рабочий стол
if [ -d "$HOME/Desktop" ]; then
    rm -rf "$HOME/Desktop/TASCAM US-1800.app"
    cp -R "$DIR/TASCAM US-1800.app" "$HOME/Desktop/"
    echo "[✓] Приложение TASCAM US-1800.app установлено на Рабочий стол"
fi

# 3. Создание папки для записей
mkdir -p "$HOME/Music/TASCAM_Recordings"
echo "[✓] Папка для записей готова: $HOME/Music/TASCAM_Recordings"

echo ""
echo "=== Установка завершена! ==="
echo "Теперь вы можете:"
echo "1. Запускать программу двойным кликом по 'TASCAM US-1800' на Рабочем столе."
echo "2. Либо в терминале выполнять команду 'tascam_usb' из любой папки."
