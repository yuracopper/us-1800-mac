#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
clear
echo "=========================================================="
echo "    Установка драйвера TASCAM US-1800 для macOS M1/M2/M3  "
echo "=========================================================="
echo ""
echo "Введите пароль администратора вашего Mac для установки:"
sudo "$DIR/mac-driver/install_hal_driver.sh"
echo ""
echo "Открываю настройки звука macOS..."
open "x-apple.systempreferences:com.apple.preference.sound"
echo ""
echo "=========================================================="
echo "Установка завершена! Выберите 'TASCAM US-1800' в списке вывода."
echo "Нажмите Enter для закрытия этого окна..."
read
