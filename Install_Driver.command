#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
clear
echo "=========================================================="
echo "   TASCAM US-1800 Native Driver Installer (Apple Silicon) "
echo "=========================================================="
echo ""
echo "Please enter your Mac administrator password to install:"
sudo "$DIR/mac-driver/install_hal_driver.sh"
echo ""
echo "Opening macOS Sound Settings..."
open "x-apple.systempreferences:com.apple.preference.sound"
echo ""
echo "=========================================================="
echo "Installation complete! Select 'TASCAM US-1800' in Sound output."
echo "Press Enter to close this window..."
read
