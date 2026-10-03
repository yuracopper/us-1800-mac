#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
clear
echo "=========================================================="
echo "    Установка драйвера TASCAM US-1800 для macOS M1-M4     "
echo "=========================================================="
echo ""
echo "Введите пароль администратора вашего Mac для установки:"
if [ -f "$DIR/mac-driver/install_hal_driver.sh" ]; then
    sudo "$DIR/mac-driver/install_hal_driver.sh"
elif [ -f "$DIR/tascam-us1800/mac-driver/install_hal_driver.sh" ]; then
    sudo "$DIR/tascam-us1800/mac-driver/install_hal_driver.sh"
elif [ -f "$HOME/Desktop/tascam-us1800/mac-driver/install_hal_driver.sh" ]; then
    sudo "$HOME/Desktop/tascam-us1800/mac-driver/install_hal_driver.sh"
fi
echo ""
echo "Открываю настройки звука macOS..."
open "x-apple.systempreferences:com.apple.preference.sound" 2>/dev/null || true
echo ""
echo "=========================================================="
echo "Установка завершена! Выберите 'TASCAM US-1800' в списке вывода."
echo "Нажмите Enter для закрытия этого окна..."
read
