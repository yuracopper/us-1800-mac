# ALSA Driver for TASCAM US-1800

## Project Status

### Implemented Features
*   **Audio Playback:**
*   **Audio Capture (Recording):** 

### To-Do & Known Limitations
* Implement MIDI IN/OUT(TBD) 

## Installation and Usage

This is an out-of-tree kernel module, meaning you must compile it against the headers for your specific kernel version.

### Step 2: Install Prerequisites (Kernel Headers & Build Tools)
You need the necessary tools to compile kernel modules and the headers for your currently running kernel. Open a terminal and run the command for your Linux distribution:

*You can attempt build without installing linux headers package first, if you are unable to build then you would need to!

*   **Debian / Ubuntu / Pop!_OS / Mint:**
    ```bash
    sudo apt update
    sudo apt install build-essential linux-headers-$(uname -r)
    ```

*   **Fedora / CentOS Stream / RHEL:**
    ```bash
    sudo dnf install kernel-devel kernel-headers make gcc
    ```

*   **Arch Linux / Manjaro:**
    ```bash
    sudo pacman -S base-devel linux-headers
    ```

*   **openSUSE:**
    ```bash
    sudo zypper install -t pattern devel_basis
    sudo zypper install kernel-devel
    ```


### Step 3: Compile and Load the Driver
This process will build the module from source and load it for your current session. This is the best way to test it.

1.  Clone this repository and navigate into the source directory.

```bash
git clone https://github.com/serifpersia/us1800.git
cd us1800/
```

2.  Compile the module:
    ```bash
    make
    ```

3.  Load the compiled module into the kernel:
    ```bash
    sudo insmod snd-usb-us1800.ko
    ```

4.  Connect your TASCAM US-1800. Verify that the driver loaded and the audio card is recognized by the system:
    ```bash
    # Check if the kernel module is loaded
    lsmod | grep snd_usb_us1800

    # Check if ALSA sees the new sound card
    aplay -l
    ```
    The first command should show `snd_usb_us1800`. The second command should list your "TASCAM US-1800 as an available playback device. You should now be able to select it in your audio settings and play sound.

### Step 4: Install for Automatic Loading on Boot
To make the driver load automatically every time you start your computer, follow these steps after you have successfully compiled it in Step 3.

You can use build_and_install script to do automate this process just `sudo chmod +x build_and_install.sh` before you run it with `./build_and_install.sh` or just do it
the manual way. To uninstall the driver run `./build_and_install.sh uninstall`

1.  **Copy the compiled module to the kernel's extra modules directory.** This makes it available to system tools.
    ```bash
    sudo mkdir -p /lib/modules/$(uname -r)/extra/us1800
    sudo cp snd-usb-us1800.ko /lib/modules/$(uname -r)/extra/us1800
    ```

2.  **Update module dependencies.** This command rebuilds the map of modules so the kernel knows about our new driver.
    ```bash
    sudo depmod -a
    ```

Now, after a reboot, the `us1800` driver should load automatically.

## Reporting Issues & Feedback

If you test this driver, please share your feedback to help improve it. Include:

- Linux distro and version  
- Kernel version (`uname -r`)  
- Exact TASCAM model  
- How you installed and loaded the driver  
- Any errors or problems (logs help)  
- Which features worked (playback, capture, MIDI)  
- Your setup details (e.g., DAW, ALSA/JACK version, buffer/periods used)

All feedback is welcome—whether it’s a bug, a success, or a suggestion!

Please report your findings via the GitHub Issues page.

## License

This project is licensed under the **GPL-2.0** see the [LICENSE](LICENSE) file for details.
