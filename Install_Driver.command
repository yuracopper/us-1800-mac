#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
clear
echo "=========================================================="
echo "   TASCAM US-1800 Native Driver Installer (Apple Silicon) "
echo "=========================================================="
echo ""
echo "Please enter your Mac administrator password to install:"
if [ -f "$DIR/mac-driver/install_hal_driver.sh" ]; then
    sudo "$DIR/mac-driver/install_hal_driver.sh"
elif [ -f "$DIR/tascam-us1800/mac-driver/install_hal_driver.sh" ]; then
    sudo "$DIR/tascam-us1800/mac-driver/install_hal_driver.sh"
elif [ -f "$HOME/Documents/GitHub/us-1800-mac/tascam-us1800/mac-driver/install_hal_driver.sh" ]; then
    sudo "$HOME/Documents/GitHub/us-1800-mac/tascam-us1800/mac-driver/install_hal_driver.sh"
elif [ -f "$HOME/Desktop/tascam-us1800/mac-driver/install_hal_driver.sh" ]; then
    sudo "$HOME/Desktop/tascam-us1800/mac-driver/install_hal_driver.sh"
fi
echo ""
echo "Opening macOS Sound Settings..."
open "x-apple.systempreferences:com.apple.preference.sound" 2>/dev/null || true
echo ""
echo "=========================================================="
echo "Installation complete! Select 'TASCAM US-1800' in Sound output."
echo "Press Enter to close this window..."
read
