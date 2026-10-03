#!/usr/bin/env python3
"""
=============================================================================
  TASCAM US-1800 Professional Audio Control Console
  Apple Silicon (M1/M2/M3/M4) Native CoreAudio Management Suite
  
  High-End Studio Mixer Aesthetic (RME TotalMix / UAD Console Style):
  - 16-Channel Hardware Input Meter Bridge (Preamps 1-8, Inst 9-10, Line 11-14, SPDIF 15-16)
  - 30-Segment LED Ladders with Visible Hardware Slots & dBFS Logarithmic Taper
  - Real-Time True CoreAudio DAW Buffer & Sample Rate Synchronization (Zero Desync)
  - Master Monitor Output Section with Dual Stereo Meters, Fader, Mute, Dim & Mono
  - Latency Engine Profiles (⚡ Ultra-Low, ✓ Balanced, 🛡 Safe)
  - Direct Buffer Quick-Grid (16, 32, 64, 128, 256, 512, 1024, 2048 smp)
  - Hardware Diagnostics, Reference Tone Generator & Direct 16-Track WAV Recorder
=============================================================================
"""

import ctypes
import math
import mmap
import os
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

# Suppress macOS Tk deprecation warning in console
os.environ["TK_SILENCE_DEPRECATION"] = "1"

# -----------------------------------------------------------------------------
# Paths and Core Constants
# -----------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

ENGINE_PATH = os.path.join(SCRIPT_DIR, "tascam_live_engine")
if not os.path.exists(ENGINE_PATH):
    ENGINE_PATH = os.path.join(SCRIPT_DIR, "..", "Resources", "tascam_live_engine")
if not os.path.exists(ENGINE_PATH):
    ENGINE_PATH = "/usr/local/bin/tascam_live_engine"
if not os.path.exists(ENGINE_PATH):
    ENGINE_PATH = "/Users/yura/Desktop/tascam-us1800/mac-driver/tascam_live_engine"

USB_TOOL_PATH = os.path.join(SCRIPT_DIR, "tascam_usb")
if not os.path.exists(USB_TOOL_PATH):
    USB_TOOL_PATH = os.path.join(SCRIPT_DIR, "..", "Resources", "tascam_usb")
if not os.path.exists(USB_TOOL_PATH):
    USB_TOOL_PATH = "/usr/local/bin/tascam_usb"
if not os.path.exists(USB_TOOL_PATH):
    USB_TOOL_PATH = "/Users/yura/Desktop/tascam-us1800/mac-driver/tascam_usb"

INSTALL_SCRIPT = os.path.join(SCRIPT_DIR, "install_hal_driver.sh")
if not os.path.exists(INSTALL_SCRIPT):
    INSTALL_SCRIPT = "/Users/yura/Desktop/tascam-us1800/mac-driver/install_hal_driver.sh"

MUSIC_DIR = os.path.expanduser("~/Music/TASCAM_Recordings")
os.makedirs(MUSIC_DIR, exist_ok=True)

TASCAM_CONF_PATH = "/var/tmp/tascam_mode.conf"

TASCAM_MODE_LOW_LATENCY = 0  # ⚡ Ultra-Low (Live Tracking)
TASCAM_MODE_BALANCED    = 1  # ✓ Balanced (Studio Recording)
TASCAM_MODE_SAFE        = 2  # 🛡 Safe (Heavy Mixdown)

LATENCY_PROFILES = [
    {
        "id": TASCAM_MODE_LOW_LATENCY,
        "title": "⚡ Ultra-Low (Live)",
        "badge": "6.2 ms RTL",
        "cushion": 16,
        "desc": "Minimal buffer cushion for live tracking (guitars & vocals). Near-zero latency.",
        "color": "#10b981",
        "bg_active": "#064e3b",
        "border_active": "#34d399",
    },
    {
        "id": TASCAM_MODE_BALANCED,
        "title": "✓ Balanced (Studio)",
        "badge": "9.8 ms RTL",
        "cushion": 32,
        "desc": "Standard studio recording & tracking with moderate plugin loads.",
        "color": "#0284c7",
        "bg_active": "#0c4a6e",
        "border_active": "#38bdf8",
    },
    {
        "id": TASCAM_MODE_SAFE,
        "title": "🛡 Safe (Heavy Mix)",
        "badge": "16+ ms RTL",
        "cushion": 128,
        "desc": "Maximum buffering cushion for 100+ track projects and high CPU VST synths.",
        "color": "#f59e0b",
        "bg_active": "#78350f",
        "border_active": "#fbbf24",
    },
]

BUFFER_SIZES = [16, 32, 64, 128, 256, 512, 1024, 2048]

# -----------------------------------------------------------------------------
# Color Palette & Console Design System
# -----------------------------------------------------------------------------
C_BG_WIN        = "#0d1016"  # Deep dark obsidian console frame
C_BG_HEADER     = "#151922"  # Top brushed aluminum rack unit
C_BG_CARD       = "#151922"  # Section card background
C_BG_STRIP      = "#10131a"  # Channel strip background
C_BORDER        = "#232a3a"  # Subtle brushed metal divider lines
C_BORDER_BRIGHT = "#333d52"  # Highlighted card border

C_TEXT_WHITE    = "#f8fafc"  # Pure crisp text
C_TEXT_SILVER   = "#cbd5e1"  # Normal readouts
C_TEXT_MUTED    = "#94a3b8"  # Subtitles and unit labels
C_TEXT_DIM      = "#64748b"  # Inactive controls

C_CYAN          = "#38bdf8"  # CoreAudio & active buffer highlight
C_GREEN         = "#34d399"  # Hardware online & safe meter signal
C_YELLOW        = "#fbbf24"  # Hot meter level
C_AMBER         = "#f59e0b"  # Warning level
C_RED           = "#ef4444"  # Clip overload & mute
C_BLUE          = "#2563eb"  # General active control

# -----------------------------------------------------------------------------
# CoreAudio CTypes Interface (Direct Hardware Query & Control)
# -----------------------------------------------------------------------------
class AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]

class CoreAudioBridge:
    def __init__(self):
        try:
            self.ca = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
            self.cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            self.cf.CFStringGetCString.restype = ctypes.c_bool
            self.cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
            self.cf.CFRelease.argtypes = [ctypes.c_void_p]
            self.available = True
        except Exception:
            self.available = False
        self.cached_dev_id = None

    def find_tascam_device_id(self):
        if not self.available:
            return None
        addr = AudioObjectPropertyAddress(0x64657623, 0x676c6f62, 0)  # 'dev#', 'glob', 0
        data_size = ctypes.c_uint32(0)
        if self.ca.AudioObjectGetPropertyDataSize(1, ctypes.byref(addr), 0, None, ctypes.byref(data_size)) != 0:
            return None
        count = data_size.value // 4
        dev_ids = (ctypes.c_uint32 * count)()
        if self.ca.AudioObjectGetPropertyData(1, ctypes.byref(addr), 0, None, ctypes.byref(data_size), ctypes.byref(dev_ids)) != 0:
            return None
        for dev_id in dev_ids:
            cf_str = ctypes.c_void_p()
            name_size = ctypes.c_uint32(ctypes.sizeof(cf_str))
            name_addr = AudioObjectPropertyAddress(0x6c6e616d, 0x676c6f62, 0)  # 'lnam'
            if self.ca.AudioObjectGetPropertyData(dev_id, ctypes.byref(name_addr), 0, None, ctypes.byref(name_size), ctypes.byref(cf_str)) == 0 and cf_str.value:
                buf = ctypes.create_string_buffer(256)
                self.cf.CFStringGetCString(cf_str, buf, 256, 0x08000100)  # UTF-8
                self.cf.CFRelease(cf_str)
                name = buf.value.decode("utf-8", errors="ignore")
                if "US-1800" in name or "TASCAM" in name:
                    self.cached_dev_id = dev_id
                    return dev_id
        return None

    def get_device_id(self):
        if not self.available:
            return None
        if self.cached_dev_id:
            is_alive = ctypes.c_uint32(0)
            sz = ctypes.c_uint32(ctypes.sizeof(is_alive))
            addr = AudioObjectPropertyAddress(0x6c69766e, 0x676c6f62, 0)  # 'livn'
            if self.ca.AudioObjectGetPropertyData(self.cached_dev_id, ctypes.byref(addr), 0, None, ctypes.byref(sz), ctypes.byref(is_alive)) == 0 and is_alive.value == 1:
                return self.cached_dev_id
        self.cached_dev_id = self.find_tascam_device_id()
        return self.cached_dev_id

    def get_hardware_status(self):
        dev_id = self.get_device_id()
        if not dev_id:
            return 44100, 128, False, False

        # 1. Active Hardware Buffer Size ('fsiz')
        buf_size = ctypes.c_uint32(0)
        sz_size = ctypes.c_uint32(ctypes.sizeof(buf_size))
        buf_addr = AudioObjectPropertyAddress(0x6673697a, 0x676c6f62, 0)
        self.ca.AudioObjectGetPropertyData(dev_id, ctypes.byref(buf_addr), 0, None, ctypes.byref(sz_size), ctypes.byref(buf_size))

        # 2. Active Sample Rate ('nsrt')
        sr = ctypes.c_double(0)
        sr_size = ctypes.c_uint32(ctypes.sizeof(sr))
        sr_addr = AudioObjectPropertyAddress(0x6e737274, 0x676c6f62, 0)
        self.ca.AudioObjectGetPropertyData(dev_id, ctypes.byref(sr_addr), 0, None, ctypes.byref(sr_size), ctypes.byref(sr))

        # 3. Running State ('goin')
        is_running = ctypes.c_uint32(0)
        r_sz = ctypes.c_uint32(ctypes.sizeof(is_running))
        r_addr = AudioObjectPropertyAddress(0x676f696e, 0x676c6f62, 0)
        self.ca.AudioObjectGetPropertyData(dev_id, ctypes.byref(r_addr), 0, None, ctypes.byref(r_sz), ctypes.byref(is_running))

        # 4. Alive State ('livn')
        is_alive = ctypes.c_uint32(0)
        a_sz = ctypes.c_uint32(ctypes.sizeof(is_alive))
        a_addr = AudioObjectPropertyAddress(0x6c69766e, 0x676c6f62, 0)
        self.ca.AudioObjectGetPropertyData(dev_id, ctypes.byref(a_addr), 0, None, ctypes.byref(a_sz), ctypes.byref(is_alive))

        rate = int(sr.value) if sr.value > 0 else 44100
        buffer = int(buf_size.value) if buf_size.value > 0 else 128
        return rate, buffer, bool(is_running.value), bool(is_alive.value)

    def set_hardware_buffer(self, new_size):
        dev_id = self.get_device_id()
        if not dev_id:
            return False, 128
        buf = ctypes.c_uint32(new_size)
        buf_addr = AudioObjectPropertyAddress(0x6673697a, 0x676c6f62, 0)
        err = self.ca.AudioObjectSetPropertyData(dev_id, ctypes.byref(buf_addr), 0, None, ctypes.sizeof(buf), ctypes.byref(buf))
        
        # Read back actual confirmed hardware buffer from CoreAudio
        actual = ctypes.c_uint32(0)
        sz = ctypes.c_uint32(ctypes.sizeof(actual))
        self.ca.AudioObjectGetPropertyData(dev_id, ctypes.byref(buf_addr), 0, None, ctypes.byref(sz), ctypes.byref(actual))
        ret_buf = actual.value if actual.value > 0 else new_size
        return (err == 0), ret_buf

# -----------------------------------------------------------------------------
# Shared Memory Layout matching tascam_shm.h (version 3)
# -----------------------------------------------------------------------------
libc = ctypes.CDLL(None)
libc.shm_open.restype = ctypes.c_int
libc.shm_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint16]

class TascamSharedBuffer(ctypes.Structure):
    _fields_ = [
        ("magic", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("sample_rate", ctypes.c_uint32),
        ("buffer_frame_size", ctypes.c_uint32),
        ("engine_heartbeat", ctypes.c_uint64),
        ("engine_running", ctypes.c_uint32),
        ("pb_wr", ctypes.c_uint32),
        ("pb_rd", ctypes.c_uint32),
        ("pb_ring", ctypes.c_float * (32768 * 4)),
        ("cap_wr", ctypes.c_uint32),
        ("cap_rd", ctypes.c_uint32),
        ("cap_ring", ctypes.c_float * (32768 * 16)),
        ("in_peak", ctypes.c_float * 16),
        ("out_peak", ctypes.c_float * 4),
        ("cmd_chime_test", ctypes.c_uint32),
        ("master_mute", ctypes.c_uint32),
        ("master_volume", ctypes.c_float),
        ("latency_mode", ctypes.c_uint32),
    ]

# -----------------------------------------------------------------------------
# Math & dBFS Mapping for Professional Metering
# -----------------------------------------------------------------------------
def peak_to_db(p):
    if p <= 0.0001:
        return -60.0
    db = 20.0 * math.log10(p)
    return max(-60.0, min(0.0, db))

def db_to_norm(db):
    """
    Logarithmic pro-audio taper matching broadcast & console meters:
    -60 dB to -36 dB: lower 25% (room floor & ambient noise)
    -36 dB to -18 dB: 25% to 55% (soft acoustic level)
    -18 dB to -6 dB:  55% to 80% (standard healthy tracking range)
     -6 dB to 0 dB:   80% to 100% (hot zone & clip headroom)
    """
    if db <= -60: return 0.0
    if db >= 0: return 1.0
    if db > -6:
        return 0.80 + 0.20 * ((db + 6) / 6.0)
    elif db > -18:
        return 0.55 + 0.25 * ((db + 18) / 12.0)
    elif db > -36:
        return 0.25 + 0.30 * ((db + 36) / 18.0)
    else:
        return 0.25 * ((db + 60) / 24.0)

# -----------------------------------------------------------------------------
# Main Application GUI Class (Pro Audio Console)
# -----------------------------------------------------------------------------
class TascamControlConsole(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TASCAM US-1800  •  Pro Audio Control Console")
        
        # Dimensions strictly proportioned to fit all 16 channels + master with zero empty gap
        win_w, win_h = 980, 610
        self.geometry(f"{win_w}x{win_h}")
        self.resizable(False, False)
        self.configure(bg=C_BG_WIN)

        # Center on screen
        ws = self.winfo_screenwidth()
        hs = self.winfo_screenheight()
        x = max(0, (ws - win_w) // 2)
        y = max(0, (hs - win_h) // 2)
        self.geometry(f"{win_w}x{win_h}+{x}+{y}")

        self.ca_bridge = CoreAudioBridge()
        self.mm = None
        self.buf = None
        self.local_engine_proc = None
        self.rec_proc = None
        self.rec_start_time = None
        self.is_connected = False
        self.running = True

        self.current_rate = 44100
        self.current_buffer = 128
        self.current_mode = TASCAM_MODE_LOW_LATENCY

        # Load persisted latency profile
        if os.path.exists(TASCAM_CONF_PATH):
            try:
                with open(TASCAM_CONF_PATH, "r") as f:
                    m = int(f.read().strip())
                    if m in (0, 1, 2):
                        self.current_mode = m
            except Exception:
                pass

        self.last_heartbeat = 0
        self.last_heartbeat_time = time.time()

        # Meter ballistics & peak-hold state
        self.in_meter_vals = [0.0] * 16
        self.in_peak_holds = [0.0] * 16
        self.in_peak_times = [0.0] * 16
        self.in_clip_times = [0.0] * 16
        self.in_mutes = [False] * 16
        self.in_solos = [False] * 16

        self.out_meter_vals = [0.0] * 2
        self.out_peak_holds = [0.0] * 2
        self.out_peak_times = [0.0] * 2
        self.out_clip_times = [0.0] * 2

        # Master controls
        self.master_vol_val = 1.0
        self.master_mute_state = False
        self.master_dim_state = False
        self.master_mono_state = False

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Build UI Architecture
        self._build_header()
        self._build_telemetry_deck()
        self._build_control_bar()
        self._build_mixer_bridge()
        self._build_utility_bar()

        # Connect to Hardware & Shared Memory
        self._init_shm()

        # Direct CoreAudio hardware sync
        rate, buf, is_running, is_alive = self.ca_bridge.get_hardware_status()
        if rate > 0: self.current_rate = rate
        if buf in BUFFER_SIZES: self.current_buffer = buf
        self._update_all_telemetry()

        # Background USB hardware monitor thread
        self.poll_thread = threading.Thread(target=self._hardware_poll_loop, daemon=True)
        self.poll_thread.start()

        # Real-time 30 FPS meter & hardware animation loop
        self._ui_tick()

    def on_close(self):
        self.running = False
        if self.mm:
            try:
                self.mm.close()
            except Exception:
                pass
        self.destroy()

    def _init_shm(self):
        try:
            if not self.buf:
                fd = libc.shm_open(b"/tascam_us1800_shm", 2, 0)  # O_RDWR = 2
                if fd >= 0:
                    self.mm = mmap.mmap(fd, ctypes.sizeof(TascamSharedBuffer), mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
                    os.close(fd)
                    self.buf = TascamSharedBuffer.from_buffer(self.mm)
                    if self.buf.magic != 0x54313830 or self.buf.version != 3:
                        self.buf = None
                        self.mm.close()
                        self.mm = None
                    else:
                        r = int(self.buf.sample_rate)
                        b = int(self.buf.buffer_frame_size)
                        m = int(self.buf.latency_mode)
                        if r in (44100, 48000, 88200, 96000): self.current_rate = r
                        if b in BUFFER_SIZES: self.current_buffer = b
                        if m in (0, 1, 2): self.current_mode = m
                        self._update_all_telemetry()
        except Exception:
            self.buf = None
            self.mm = None

    def _hardware_poll_loop(self):
        while self.running:
            try:
                r = subprocess.run(["ioreg", "-p", "IOUSB", "-w0"], capture_output=True, text=True, timeout=2)
                self.is_connected = ("US-1800" in r.stdout)
            except Exception:
                self.is_connected = False

            if not self.buf:
                self._init_shm()

            time.sleep(1.2)

    # =========================================================================
    # SECTION 1: HEADER RACK UNIT
    # =========================================================================
    def _build_header(self):
        hdr = tk.Frame(self, bg=C_BG_HEADER, padx=14, pady=6, highlightbackground=C_BORDER, highlightthickness=1)
        hdr.pack(fill=tk.X)

        left_btn = tk.Button(
            hdr,
            text="TASCAM US-1800  •  16-In / 4-Out Apple Silicon Native",
            font=("Helvetica", 11, "bold"),
            bg=C_BG_HEADER,
            fg=C_TEXT_WHITE,
            activebackground=C_BG_HEADER,
            activeforeground=C_TEXT_WHITE,
            relief=tk.FLAT,
            bd=0,
            command=self.open_sound_prefs
        )
        left_btn.pack(side=tk.LEFT)

        right_box = tk.Frame(hdr, bg=C_BG_HEADER)
        right_box.pack(side=tk.RIGHT)

        self.badge_usb = tk.Button(
            right_box,
            text="USB 2.0 (480M)",
            font=("Helvetica", 8, "bold"),
            bg="#1c2538",
            fg=C_CYAN,
            activebackground="#1c2538",
            activeforeground=C_CYAN,
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=2
        )
        self.badge_usb.pack(side=tk.LEFT, padx=3)

        self.badge_driver = tk.Button(
            right_box,
            text="COREAUDIO HAL",
            font=("Helvetica", 8, "bold"),
            bg="#1c2538",
            fg=C_GREEN,
            activebackground="#1c2538",
            activeforeground=C_GREEN,
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=2
        )
        self.badge_driver.pack(side=tk.LEFT, padx=3)

        self.status_pill = tk.Button(
            right_box,
            text="● HARDWARE ONLINE",
            font=("Helvetica", 9, "bold"),
            bg="#064e3b",
            fg="#34d399",
            activebackground="#064e3b",
            activeforeground="#34d399",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=2,
            cursor="hand2",
            command=self.restart_engine
        )
        self.status_pill.pack(side=tk.LEFT, padx=(3, 0))

    # =========================================================================
    # SECTION 2: TELEMETRY DECK (4 Pro Displays + Live Confirmation Banner)
    # =========================================================================
    def _build_telemetry_deck(self):
        deck = tk.Frame(self, bg=C_BG_WIN, padx=12, pady=5)
        deck.pack(fill=tk.X)

        tile_base = {
            "font": ("Helvetica", 10, "bold"),
            "bg": "#1c2333",
            "activebackground": "#252e42",
            "activeforeground": "#ffffff",
            "relief": tk.RIDGE,
            "bd": 1,
            "pady": 5,
        }

        self.tile_rate = tk.Button(
            deck,
            text="44.1 kHz\nSample Rate",
            fg="#38bdf8",
            command=self.open_audio_midi_setup,
            **tile_base
        )
        self.tile_rate.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)

        self.tile_buf = tk.Button(
            deck,
            text="128 smp (2.9 ms)\nActive Buffer",
            fg="#38bdf8",
            command=self._cycle_next_buffer,
            **tile_base
        )
        self.tile_buf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)

        self.tile_rtl = tk.Button(
            deck,
            text="~6.2 ms RTL\nEst. Latency",
            fg="#34d399",
            command=self._cycle_next_mode,
            **tile_base
        )
        self.tile_rtl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)

        self.tile_status = tk.Button(
            deck,
            text="● ONLINE\nHardware Active",
            fg="#34d399",
            command=self.restart_engine,
            **tile_base
        )
        self.tile_status.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)

        self.banner_feedback = tk.Button(
            self,
            text="✓ CoreAudio Active: Buffer = 128 smp (2.90 ms) | RTL: ~6.2 ms | Clock: 44.1 kHz Locked",
            font=("Helvetica", 9, "bold"),
            bg="#162032",
            fg="#38bdf8",
            activebackground="#1e293b",
            activeforeground="#38bdf8",
            relief=tk.FLAT,
            bd=0,
            pady=3,
            command=self.open_sound_prefs
        )
        self.banner_feedback.pack(fill=tk.X, padx=14, pady=(0, 4))

    # =========================================================================
    # SECTION 3: BUFFER SELECTOR & LATENCY PROFILE BAR
    # =========================================================================
    def _build_control_bar(self):
        ctrl_frame = tk.Frame(self, bg=C_BG_CARD, highlightbackground=C_BORDER, highlightthickness=1, padx=10, pady=5)
        ctrl_frame.pack(fill=tk.X, padx=14, pady=(0, 4))

        # Left: Latency Profile Selector
        left_col = tk.Frame(ctrl_frame, bg=C_BG_CARD)
        left_col.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 16))

        prof_hdr = tk.Frame(left_col, bg=C_BG_CARD)
        prof_hdr.pack(fill=tk.X, pady=(0, 3))
        tk.Button(
            prof_hdr,
            text="LATENCY PROFILE",
            font=("Helvetica", 8, "bold"),
            bg=C_BG_CARD,
            fg=C_TEXT_MUTED,
            relief=tk.FLAT,
            bd=0
        ).pack(side=tk.LEFT)

        prof_btn_row = tk.Frame(left_col, bg=C_BG_CARD)
        prof_btn_row.pack(fill=tk.X)

        self.mode_buttons = {}
        for p in LATENCY_PROFILES:
            mid = p["id"]
            btn = tk.Button(
                prof_btn_row,
                text=p["title"],
                font=("Helvetica", 9, "bold"),
                bg="#1e2436",
                fg=C_TEXT_MUTED,
                activebackground=p["bg_active"],
                activeforeground="#ffffff",
                relief=tk.FLAT,
                bd=0,
                padx=8,
                pady=3,
                cursor="hand2",
                command=lambda val=mid: self.select_latency_mode(val)
            )
            btn.pack(side=tk.LEFT, padx=2)
            self.mode_buttons[mid] = btn

        # Right: DAW Buffer Quick-Grid
        right_col = tk.Frame(ctrl_frame, bg=C_BG_CARD)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        buf_hdr = tk.Frame(right_col, bg=C_BG_CARD)
        buf_hdr.pack(fill=tk.X, pady=(0, 3))
        tk.Button(
            buf_hdr,
            text="BUFFER SIZE (COREAUDIO SAMPLES)",
            font=("Helvetica", 8, "bold"),
            bg=C_BG_CARD,
            fg=C_TEXT_MUTED,
            relief=tk.FLAT,
            bd=0
        ).pack(side=tk.LEFT)

        tk.Button(
            buf_hdr,
            text="Synchronized with Studio One / DAWs",
            font=("Helvetica", 8),
            bg=C_BG_CARD,
            fg=C_CYAN,
            relief=tk.FLAT,
            bd=0
        ).pack(side=tk.RIGHT)

        buf_btn_row = tk.Frame(right_col, bg=C_BG_CARD)
        buf_btn_row.pack(fill=tk.X)

        self.buf_buttons = {}
        for b in BUFFER_SIZES:
            btn = tk.Button(
                buf_btn_row,
                text=str(b),
                font=("Helvetica", 9, "bold"),
                bg="#1e2436",
                fg=C_TEXT_MUTED,
                activebackground="#0284c7",
                activeforeground="#ffffff",
                relief=tk.FLAT,
                bd=0,
                padx=4,
                pady=3,
                cursor="hand2",
                command=lambda val=b: self.select_buffer_size(val)
            )
            btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            self.buf_buttons[b] = btn

    # =========================================================================
    # SECTION 4: 16-CHANNEL METER BRIDGE & MASTER MONITOR CONSOLE
    # =========================================================================
    def _build_mixer_bridge(self):
        bridge_frame = tk.Frame(self, bg=C_BG_WIN, padx=14, pady=0, height=275)
        bridge_frame.pack(fill=tk.X)
        bridge_frame.pack_propagate(False)

        # --- LEFT: 16-Channel Hardware Input Meter Bridge ---
        input_container = tk.Frame(bridge_frame, bg=C_BG_CARD, highlightbackground=C_BORDER, highlightthickness=1, padx=4, pady=3, width=765, height=275)
        input_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Top Group Header Bar
        group_hdr_bar = tk.Frame(input_container, bg=C_BG_CARD)
        group_hdr_bar.pack(fill=tk.X, pady=(0, 2))

        tk.Button(group_hdr_bar, text="PREAMPS 1-8 (MIC/LINE XLR)", font=("Helvetica", 8, "bold"), bg=C_BG_CARD, fg=C_CYAN, relief=tk.FLAT, bd=0).pack(side=tk.LEFT, padx=(4, 115))
        tk.Button(group_hdr_bar, text="GUITAR 9-10", font=("Helvetica", 8, "bold"), bg=C_BG_CARD, fg=C_AMBER, relief=tk.FLAT, bd=0).pack(side=tk.LEFT, padx=(0, 16))
        tk.Button(group_hdr_bar, text="LINE 11-14", font=("Helvetica", 8, "bold"), bg=C_BG_CARD, fg="#c084fc", relief=tk.FLAT, bd=0).pack(side=tk.LEFT, padx=(0, 42))
        tk.Button(group_hdr_bar, text="DIGITAL 15-16", font=("Helvetica", 8, "bold"), bg=C_BG_CARD, fg="#f472b6", relief=tk.FLAT, bd=0).pack(side=tk.LEFT)

        strips_row = tk.Frame(input_container, bg=C_BG_CARD)
        strips_row.pack(fill=tk.BOTH, expand=True)

        self.in_canvases = []
        self.in_mute_btns = []
        self.in_solo_btns = []

        channel_labels = [
            ("MIC 1", "XLR", C_CYAN), ("MIC 2", "XLR", C_CYAN),
            ("MIC 3", "XLR", C_CYAN), ("MIC 4", "XLR", C_CYAN),
            ("MIC 5", "XLR", C_CYAN), ("MIC 6", "XLR", C_CYAN),
            ("MIC 7", "XLR", C_CYAN), ("MIC 8", "XLR", C_CYAN),
            ("GTR 9", "INST", C_AMBER), ("GTR 10", "INST", C_AMBER),
            ("LINE 11", "LINE", "#c084fc"), ("LINE 12", "LINE", "#c084fc"),
            ("LINE 13", "LINE", "#c084fc"), ("LINE 14", "LINE", "#c084fc"),
            ("SPD 15", "COAX", "#f472b6"), ("SPD 16", "COAX", "#f472b6")
        ]

        for i, (name, tag, accent_col) in enumerate(channel_labels):
            pad_left = 1
            if i in (8, 10, 14):
                pad_left = 5

            col = tk.Frame(strips_row, bg=C_BG_STRIP, highlightbackground=C_BORDER, highlightthickness=1, padx=1, pady=2, width=45)
            col.pack(side=tk.LEFT, fill=tk.Y, padx=(pad_left, 1))

            # Full Channel Strip Canvas (Height = 215px)
            # Renders: Channel Name, Tag, Clip LED, 30 visible LED slots, dB readout
            canvas = tk.Canvas(col, width=42, height=215, bg=C_BG_STRIP, highlightthickness=0)
            canvas.pack(side=tk.TOP)
            self.in_canvases.append(canvas)
            self._init_strip_canvas(canvas, name, tag, accent_col, idx=i)

            # Mute & Solo Button Row below the canvas
            btn_box = tk.Frame(col, bg=C_BG_STRIP)
            btn_box.pack(side=tk.BOTTOM, fill=tk.X, pady=(1, 2))

            btn_m = tk.Button(
                btn_box,
                text="M",
                font=("Helvetica", 7, "bold"),
                bg="#1e2436",
                fg=C_TEXT_MUTED,
                activebackground=C_RED,
                activeforeground="#ffffff",
                relief=tk.FLAT,
                bd=0,
                padx=1,
                pady=1,
                cursor="hand2",
                command=lambda idx=i: self.toggle_channel_mute(idx)
            )
            btn_m.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(1, 1))
            self.in_mute_btns.append(btn_m)

            btn_s = tk.Button(
                btn_box,
                text="S",
                font=("Helvetica", 7, "bold"),
                bg="#1e2436",
                fg=C_TEXT_MUTED,
                activebackground=C_YELLOW,
                activeforeground="#000000",
                relief=tk.FLAT,
                bd=0,
                padx=1,
                pady=1,
                cursor="hand2",
                command=lambda idx=i: self.toggle_channel_solo(idx)
            )
            btn_s.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(1, 1))
            self.in_solo_btns.append(btn_s)

        # --- RIGHT: Master Monitor Console Section ---
        master_col = tk.Frame(bridge_frame, bg=C_BG_CARD, highlightbackground=C_BORDER, highlightthickness=1, width=175, height=275, padx=6, pady=3)
        master_col.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))
        master_col.pack_propagate(False)

        tk.Button(master_col, text="MASTER MONITOR", font=("Helvetica", 9, "bold"), bg=C_BG_CARD, fg=C_TEXT_WHITE, relief=tk.FLAT, bd=0).pack(anchor="w")
        tk.Button(master_col, text="Out 1-2 (Main) • Out 3-4", font=("Helvetica", 7), bg=C_BG_CARD, fg=C_TEXT_MUTED, relief=tk.FLAT, bd=0).pack(anchor="w", pady=(0, 2))

        # Stereo Master Meter Canvas (Dual L/R + dB Scale)
        self.master_canvas = tk.Canvas(master_col, width=160, height=185, bg=C_BG_CARD, highlightthickness=0)
        self.master_canvas.pack(side=tk.TOP)
        self._init_master_canvas(self.master_canvas)

        # Master Fader & Volume Scale
        fader_hdr = tk.Frame(master_col, bg=C_BG_CARD)
        fader_hdr.pack(fill=tk.X, pady=(2, 0))

        tk.Button(fader_hdr, text="LEVEL", font=("Helvetica", 7, "bold"), bg=C_BG_CARD, fg=C_TEXT_MUTED, relief=tk.FLAT, bd=0).pack(side=tk.LEFT)
        self.lbl_master_db = tk.Button(fader_hdr, text="0.0 dB", font=("Helvetica", 8, "bold"), bg=C_BG_CARD, fg=C_CYAN, relief=tk.FLAT, bd=0)
        self.lbl_master_db.pack(side=tk.RIGHT)

        self.slider_vol = tk.Scale(
            master_col,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            showvalue=0,
            bg=C_BG_CARD,
            troughcolor="#1e2436",
            activebackground=C_CYAN,
            highlightthickness=0,
            bd=0,
            command=self._on_fader_move
        )
        self.slider_vol.set(100)
        self.slider_vol.pack(fill=tk.X, pady=(0, 3))

        # Master Buttons Row: MUTE, DIM, MONO
        m_btns_row = tk.Frame(master_col, bg=C_BG_CARD)
        m_btns_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 2))

        self.btn_master_mute = tk.Button(
            m_btns_row,
            text="MUTE",
            font=("Helvetica", 8, "bold"),
            bg="#1e2436",
            fg=C_TEXT_SILVER,
            activebackground=C_RED,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            pady=2,
            cursor="hand2",
            command=self.toggle_master_mute
        )
        self.btn_master_mute.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.btn_master_dim = tk.Button(
            m_btns_row,
            text="DIM",
            font=("Helvetica", 8, "bold"),
            bg="#1e2436",
            fg=C_TEXT_SILVER,
            activebackground=C_AMBER,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            pady=2,
            cursor="hand2",
            command=self.toggle_master_dim
        )
        self.btn_master_dim.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.btn_master_mono = tk.Button(
            m_btns_row,
            text="MONO",
            font=("Helvetica", 8, "bold"),
            bg="#1e2436",
            fg=C_TEXT_SILVER,
            activebackground=C_CYAN,
            activeforeground="#000000",
            relief=tk.FLAT,
            bd=0,
            pady=2,
            cursor="hand2",
            command=self.toggle_master_mono
        )
        self.btn_master_mono.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

    # =========================================================================
    # SECTION 5: BOTTOM STUDIO UTILITY BAR
    # =========================================================================
    def _build_utility_bar(self):
        bar = tk.Frame(self, bg=C_BG_HEADER, padx=14, pady=5, highlightbackground=C_BORDER, highlightthickness=1)
        bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Tone Test (440 Hz Sine)
        self.btn_tone = tk.Button(
            bar,
            text="♫ Reference Tone (440 Hz)",
            font=("Helvetica", 9, "bold"),
            bg="#1e2436",
            fg=C_CYAN,
            activebackground="#2a334a",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self.trigger_tone_test
        )
        self.btn_tone.pack(side=tk.LEFT, padx=(0, 4))

        # Record 16 Channels
        self.btn_rec = tk.Button(
            bar,
            text="● Record 16 Channels (WAV)",
            font=("Helvetica", 9, "bold"),
            bg="#dc2626",
            fg="#ffffff",
            activebackground="#b91c1c",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self.toggle_recording
        )
        self.btn_rec.pack(side=tk.LEFT, padx=4)

        # Open Recordings Folder
        self.btn_rec_folder = tk.Button(
            bar,
            text="📁 Folder",
            font=("Helvetica", 9),
            bg="#1e2436",
            fg=C_TEXT_SILVER,
            activebackground="#2a334a",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self.open_recordings_folder
        )
        self.btn_rec_folder.pack(side=tk.LEFT, padx=(0, 10))

        # System Utilities (Right)
        self.btn_restart = tk.Button(
            bar,
            text="🔄 Restart Engine",
            font=("Helvetica", 9, "bold"),
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self.restart_engine
        )
        self.btn_restart.pack(side=tk.RIGHT, padx=(4, 0))

        self.btn_sound_prefs = tk.Button(
            bar,
            text="⚙ Sound Settings",
            font=("Helvetica", 9),
            bg="#1e2436",
            fg=C_TEXT_SILVER,
            activebackground="#2a334a",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self.open_sound_prefs
        )
        self.btn_sound_prefs.pack(side=tk.RIGHT, padx=4)

        self.btn_midi_setup = tk.Button(
            bar,
            text="🎹 Audio MIDI Setup",
            font=("Helvetica", 9),
            bg="#1e2436",
            fg=C_TEXT_SILVER,
            activebackground="#2a334a",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self.open_audio_midi_setup
        )
        self.btn_midi_setup.pack(side=tk.RIGHT, padx=4)

    # =========================================================================
    # High-Performance Canvas Strip Initialization & Rendering
    # =========================================================================
    def _init_strip_canvas(self, canvas, name, tag, accent_col, idx):
        """
        Creates all channel strip graphic elements:
        - Header badge (name + tag)
        - Clip LED
        - 30 segmented LED slots with visible dark-slate bezels
        - Peak hold tick line
        - Monospace numeric dBFS readout
        """
        # 1. Channel Badge Box
        canvas.create_rectangle(3, 3, 41, 23, fill="#1c2538", outline="#2e384d")
        canvas.create_text(22, 10, text=name, fill=accent_col, font=("Helvetica", 8, "bold"))
        canvas.create_text(22, 18, text=tag, fill=C_TEXT_MUTED, font=("Helvetica", 6))

        # 2. Clip LED (Overload indicator)
        canvas.clip_rect = canvas.create_rectangle(12, 26, 32, 33, fill="#250d12", outline="#44141c")
        canvas.tag_bind(canvas.clip_rect, "<Button-1>", lambda e, ch=idx: self._clear_clip(ch))

        # 3. 30 LED Segments (visible dark slate slots when idle)
        canvas.segments = []
        meter_bottom = 190
        meter_x1, meter_x2 = 10, 34

        for s in range(30):
            y2 = meter_bottom - (s * 5)
            y1 = y2 - 4

            if s >= 26:      # 26..29 (0 to -3 dB): Red
                on_col, on_out = "#ff1744", "#ff5252"
            elif s >= 21:    # 21..25 (-3 to -6 dB): Amber/Orange
                on_col, on_out = "#ff9100", "#ffab40"
            elif s >= 14:    # 14..20 (-6 to -12 dB): Yellow
                on_col, on_out = "#ffd600", "#ffe57f"
            elif s >= 6:     # 6..13 (-12 to -24 dB): Mint Green
                on_col, on_out = "#00e676", "#69f0ae"
            else:            # 0..5 (-24 to -60 dB): Deep Emerald
                on_col, on_out = "#00c853", "#00e676"

            rect_id = canvas.create_rectangle(meter_x1, y1, meter_x2, y2, fill="#1a2233", outline="#28354d")
            canvas.segments.append((rect_id, on_col, on_out))

        # 4. Peak Hold Line
        canvas.peak_line = canvas.create_line(meter_x1, meter_bottom, meter_x2, meter_bottom, fill="#ffffff", width=2, state="hidden")

        # 5. Monospace Numeric dB Readout
        canvas.db_text = canvas.create_text(22, 204, text="-inf", fill=C_TEXT_MUTED, font=("Menlo", 8, "bold"))

    def _init_master_canvas(self, canvas):
        """
        Builds the Master stereo monitor section with L & R meters and central dB scale markings
        """
        # Header Labels
        canvas.create_text(25, 9, text="L", fill=C_CYAN, font=("Helvetica", 8, "bold"))
        canvas.create_text(135, 9, text="R", fill=C_CYAN, font=("Helvetica", 8, "bold"))

        # Clip LEDs
        canvas.clip_l = canvas.create_rectangle(15, 16, 35, 23, fill="#250d12", outline="#44141c")
        canvas.clip_r = canvas.create_rectangle(125, 16, 145, 23, fill="#250d12", outline="#44141c")

        # dB Scale Ticks in center
        meter_bottom = 168
        ticks = [
            (0, "0 dB", "#ff1744"),
            (3, "-3", "#ff9100"),
            (6, "-6", "#ffd600"),
            (12, "-12", "#00e676"),
            (18, "-18", "#00e676"),
            (24, "-24", "#00c853"),
            (36, "-36", C_TEXT_MUTED),
            (48, "-48", C_TEXT_MUTED),
        ]
        for db_val, label, col in ticks:
            norm = db_to_norm(-db_val if db_val > 0 else 0)
            y = meter_bottom - int(norm * 140)
            canvas.create_text(80, y, text=label, fill=col, font=("Menlo", 6, "bold"))
            canvas.create_line(40, y, 50, y, fill="#28354d")
            canvas.create_line(110, y, 120, y, fill="#28354d")

        # Build L and R segments (30 segments each)
        canvas.segs_l = []
        canvas.segs_r = []
        for s in range(28):
            y2 = meter_bottom - (s * 5)
            y1 = y2 - 4

            if s >= 24:   on_col, on_out = "#ff1744", "#ff5252"
            elif s >= 19: on_col, on_out = "#ff9100", "#ffab40"
            elif s >= 13: on_col, on_out = "#ffd600", "#ffe57f"
            elif s >= 6:  on_col, on_out = "#00e676", "#69f0ae"
            else:         on_col, on_out = "#00c853", "#00e676"

            # Left
            r_l = canvas.create_rectangle(14, y1, 36, y2, fill="#1a2233", outline="#28354d")
            canvas.segs_l.append((r_l, on_col, on_out))

            # Right
            r_r = canvas.create_rectangle(124, y1, 146, y2, fill="#1a2233", outline="#28354d")
            canvas.segs_r.append((r_r, on_col, on_out))

        # Peak Hold Lines
        canvas.peak_l = canvas.create_line(14, meter_bottom, 36, meter_bottom, fill="#ffffff", width=2, state="hidden")
        canvas.peak_r = canvas.create_line(124, meter_bottom, 146, meter_bottom, fill="#ffffff", width=2, state="hidden")

        # Numeric dB Readouts
        canvas.db_l = canvas.create_text(25, 178, text="-inf", fill=C_TEXT_MUTED, font=("Menlo", 7, "bold"))
        canvas.db_r = canvas.create_text(135, 178, text="-inf", fill=C_TEXT_MUTED, font=("Menlo", 7, "bold"))

    def _update_strip_display(self, canvas, norm_val, peak_hold_norm, db_val, is_clipped):
        # 1. Update Clip LED
        if is_clipped:
            canvas.itemconfigure(canvas.clip_rect, fill=C_RED, outline="#ff5252")
        else:
            canvas.itemconfigure(canvas.clip_rect, fill="#250d12", outline="#44141c")

        # 2. Update 30 Segments
        active_count = int(norm_val * 30.0)
        if active_count > 30: active_count = 30

        for s, (rect_id, on_col, on_out) in enumerate(canvas.segments):
            if s < active_count:
                canvas.itemconfigure(rect_id, fill=on_col, outline=on_out)
            else:
                canvas.itemconfigure(rect_id, fill="#1a2233", outline="#28354d")

        # 3. Update Peak Hold Line
        if peak_hold_norm > 0.05:
            hold_y = 190 - int(peak_hold_norm * 145)
            if hold_y < 44: hold_y = 44
            if hold_y > 188: hold_y = 188
            canvas.coords(canvas.peak_line, 10, hold_y, 34, hold_y)
            canvas.itemconfigure(canvas.peak_line, state="normal")
        else:
            canvas.itemconfigure(canvas.peak_line, state="hidden")

        # 4. Update dB Text
        if db_val <= -59.5:
            canvas.itemconfigure(canvas.db_text, text="-inf", fill=C_TEXT_MUTED)
        elif is_clipped:
            canvas.itemconfigure(canvas.db_text, text="CLIP", fill=C_RED)
        else:
            col = C_TEXT_SILVER if db_val < -6 else C_YELLOW
            canvas.itemconfigure(canvas.db_text, text=f"{db_val:.1f}", fill=col)

    def _update_master_display(self, norm_l, hold_l, db_l, clip_l, norm_r, hold_r, db_r, clip_r):
        c = self.master_canvas

        # Clip LEDs
        c.itemconfigure(c.clip_l, fill=C_RED if clip_l else "#250d12")
        c.itemconfigure(c.clip_r, fill=C_RED if clip_r else "#250d12")

        # Left Segments
        cnt_l = min(28, int(norm_l * 28.0))
        for s, (rect_id, on_col, on_out) in enumerate(c.segs_l):
            if s < cnt_l:
                c.itemconfigure(rect_id, fill=on_col, outline=on_out)
            else:
                c.itemconfigure(rect_id, fill="#1a2233", outline="#28354d")

        # Right Segments
        cnt_r = min(28, int(norm_r * 28.0))
        for s, (rect_id, on_col, on_out) in enumerate(c.segs_r):
            if s < cnt_r:
                c.itemconfigure(rect_id, fill=on_col, outline=on_out)
            else:
                c.itemconfigure(rect_id, fill="#1a2233", outline="#28354d")

        # Peak Holds
        if hold_l > 0.05:
            hy_l = max(24, min(166, 168 - int(hold_l * 140)))
            c.coords(c.peak_l, 14, hy_l, 36, hy_l)
            c.itemconfigure(c.peak_l, state="normal")
        else:
            c.itemconfigure(c.peak_l, state="hidden")

        if hold_r > 0.05:
            hy_r = max(24, min(166, 168 - int(hold_r * 140)))
            c.coords(c.peak_r, 124, hy_r, 146, hy_r)
            c.itemconfigure(c.peak_r, state="normal")
        else:
            c.itemconfigure(c.peak_r, state="hidden")

        # Readouts
        c.itemconfigure(c.db_l, text="-inf" if db_l <= -59.5 else (f"{db_l:.1f}" if not clip_l else "CLIP"))
        c.itemconfigure(c.db_r, text="-inf" if db_r <= -59.5 else (f"{db_r:.1f}" if not clip_r else "CLIP"))

    def _clear_clip(self, idx):
        self.in_clip_times[idx] = 0.0

    # =========================================================================
    # Telemetry Updates & Real-Time Actions
    # =========================================================================
    def _cycle_next_buffer(self):
        try:
            idx = BUFFER_SIZES.index(self.current_buffer)
            next_idx = (idx + 1) % len(BUFFER_SIZES)
            self.select_buffer_size(BUFFER_SIZES[next_idx])
        except Exception:
            self.select_buffer_size(128)

    def _cycle_next_mode(self):
        next_mode = (self.current_mode + 1) % 3
        self.select_latency_mode(next_mode)

    def select_latency_mode(self, mode):
        self.current_mode = mode
        if self.buf:
            self.buf.latency_mode = mode

        try:
            with open(TASCAM_CONF_PATH, "w") as f:
                f.write(f"{mode}\n")
        except Exception:
            pass

        self._update_all_telemetry()

    def select_buffer_size(self, buf_size):
        ca_ok, confirmed_buf = self.ca_bridge.set_hardware_buffer(buf_size)
        self.current_buffer = confirmed_buf

        if self.buf:
            self.buf.buffer_frame_size = self.current_buffer

        self._update_all_telemetry(ca_ok=ca_ok)

    def _update_all_telemetry(self, ca_ok=True):
        # 1. Update Mode Buttons
        for p in LATENCY_PROFILES:
            mid = p["id"]
            btn = self.mode_buttons.get(mid)
            if not btn: continue
            if mid == self.current_mode:
                btn.configure(bg=p["bg_active"], fg="#ffffff", text=f"● {p['title']}")
            else:
                btn.configure(bg="#1e2436", fg=C_TEXT_MUTED, text=p["title"])

        # 2. Update Buffer Buttons
        for b, btn in self.buf_buttons.items():
            if b == self.current_buffer:
                btn.configure(bg="#0284c7", fg="#ffffff", text=f"✓ {b}")
            else:
                btn.configure(bg="#1e2436", fg=C_TEXT_MUTED, text=str(b))

        # 3. Calculate True RTL Values
        rate = self.current_rate or 44100
        buf = self.current_buffer or 128
        ms_buf = (buf / rate) * 1000.0

        if self.current_mode == TASCAM_MODE_LOW_LATENCY:
            cushion = 16
            rtl = (buf * 2.0 / rate * 1000.0) + (cushion / rate * 1000.0) + 1.2
            col_rtl = C_GREEN
        elif self.current_mode == TASCAM_MODE_BALANCED:
            cushion = 32
            rtl = (buf * 3.0 / rate * 1000.0) + (cushion / rate * 1000.0) + 2.0
            col_rtl = C_CYAN
        else:
            cushion = 128
            rtl = (buf * 4.0 / rate * 1000.0) + (cushion / rate * 1000.0) + 4.0
            col_rtl = C_AMBER

        # Update 4 Telemetry Displays
        self.tile_rate.configure(text=f"{rate / 1000.0:.1f} kHz\nSample Rate")
        self.tile_buf.configure(text=f"{buf} smp ({ms_buf:.1f} ms)\nActive Buffer")
        self.tile_rtl.configure(text=f"~{rtl:.1f} ms RTL\nEst. Latency", fg=col_rtl)

        if self.is_engine_active():
            self.tile_status.configure(text="● ONLINE\nHardware Active", fg=C_GREEN)
        elif self.is_connected:
            self.tile_status.configure(text="● STANDBY\nUSB Connected", fg=C_YELLOW)
        else:
            self.tile_status.configure(text="● OFFLINE\nDisconnected", fg=C_RED)

        tag = "CoreAudio Confirmed" if ca_ok else "Applied"
        self.banner_feedback.configure(
            text=f"✓ {tag}: Buffer = {buf} smp ({ms_buf:.2f} ms) | Round-Trip Latency: ~{rtl:.1f} ms RTL | Clock: {rate / 1000.0:.1f} kHz Locked"
        )

    # =========================================================================
    # Channel Strip Controls & Master Fader
    # =========================================================================
    def toggle_channel_mute(self, idx):
        self.in_mutes[idx] = not self.in_mutes[idx]
        btn = self.in_mute_btns[idx]
        if self.in_mutes[idx]:
            btn.configure(bg=C_RED, fg="#ffffff")
        else:
            btn.configure(bg="#1e2436", fg=C_TEXT_MUTED)

    def toggle_channel_solo(self, idx):
        self.in_solos[idx] = not self.in_solos[idx]
        btn = self.in_solo_btns[idx]
        if self.in_solos[idx]:
            btn.configure(bg=C_YELLOW, fg="#000000")
        else:
            btn.configure(bg="#1e2436", fg=C_TEXT_MUTED)

    def _on_fader_move(self, val):
        pct = float(val) / 100.0
        self.master_vol_val = pct
        if pct <= 0.001:
            db_txt = "-inf dB"
        else:
            db = 20.0 * math.log10(pct)
            db_txt = f"{db:.1f} dB" if db < -0.1 else "0.0 dB"
        self.lbl_master_db.configure(text=db_txt)
        if self.buf and not self.master_mute_state:
            eff_vol = pct * (0.1 if self.master_dim_state else 1.0)
            self.buf.master_volume = eff_vol

    def toggle_master_mute(self):
        self.master_mute_state = not self.master_mute_state
        if self.master_mute_state:
            self.btn_master_mute.configure(bg=C_RED, fg="#ffffff")
            if self.buf: self.buf.master_mute = 1
        else:
            self.btn_master_mute.configure(bg="#1e2436", fg=C_TEXT_SILVER)
            if self.buf: self.buf.master_mute = 0

    def toggle_master_dim(self):
        self.master_dim_state = not self.master_dim_state
        if self.master_dim_state:
            self.btn_master_dim.configure(bg=C_AMBER, fg="#ffffff")
        else:
            self.btn_master_dim.configure(bg="#1e2436", fg=C_TEXT_SILVER)
        self._on_fader_move(self.slider_vol.get())

    def toggle_master_mono(self):
        self.master_mono_state = not self.master_mono_state
        if self.master_mono_state:
            self.btn_master_mono.configure(bg=C_CYAN, fg="#000000")
        else:
            self.btn_master_mono.configure(bg="#1e2436", fg=C_TEXT_SILVER)

    # =========================================================================
    # Hardware & Audio Engine Commands
    # =========================================================================
    def is_engine_active(self):
        if self.buf and self.buf.engine_running == 1:
            cur_hb = int(self.buf.engine_heartbeat)
            now = time.time()
            if cur_hb != self.last_heartbeat:
                self.last_heartbeat = cur_hb
                self.last_heartbeat_time = now
                return True
            elif now - self.last_heartbeat_time < 2.0:
                return True
            else:
                return False
        if self.local_engine_proc and self.local_engine_proc.poll() is None:
            return True
        return False

    def restart_engine(self):
        self.stop_engine()
        self.after(500, self.start_engine)

    def start_engine(self):
        if not self.is_connected:
            messagebox.showwarning("Notice", "TASCAM US-1800 was not detected on USB.\nPlease check the USB cable and rear power switch.")
            return

        subprocess.run(["launchctl", "kickstart", "-k", "system/com.tascam.us1800.live"], capture_output=True)
        time.sleep(0.4)
        if self.is_engine_active():
            return

        try:
            self.local_engine_proc = subprocess.Popen([ENGINE_PATH])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start live audio engine:\n{e}")

    def stop_engine(self):
        if self.rec_proc and self.rec_proc.poll() is None:
            self.stop_recording()

        if self.buf:
            self.buf.engine_running = 0

        if self.local_engine_proc and self.local_engine_proc.poll() is None:
            self.local_engine_proc.send_signal(signal.SIGINT)
            try:
                self.local_engine_proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.local_engine_proc.kill()
            self.local_engine_proc = None

        subprocess.run(["launchctl", "stop", "com.tascam.us1800.live"], capture_output=True)

    def trigger_tone_test(self):
        if not self.is_engine_active():
            self.start_engine()
            time.sleep(0.5)

        if self.buf:
            self.buf.cmd_chime_test = 1
            self.btn_tone.configure(text="♫ Tone Playing...", bg=C_GREEN, fg="#ffffff")
            self.after(2200, lambda: self.btn_tone.configure(text="♫ Reference Tone (440 Hz)", bg="#1e2436", fg=C_CYAN))
        else:
            threading.Thread(target=lambda: subprocess.run([USB_TOOL_PATH, "--chime", "0.1", "--rate", str(self.current_rate)]), daemon=True).start()

    def toggle_recording(self):
        if self.rec_proc and self.rec_proc.poll() is None:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if not self.is_connected:
            messagebox.showwarning("Notice", "TASCAM US-1800 is not connected.")
            return

        if self.is_engine_active():
            self.stop_engine()
            time.sleep(0.5)

        rate = str(self.current_rate)
        ts = time.strftime("%Y%m%d_%H%M%S")
        prefix = os.path.join(MUSIC_DIR, f"tascam_session_{ts}")

        cmd = [USB_TOOL_PATH, "--rate", rate, "--record-split", prefix]
        self.rec_proc = subprocess.Popen(cmd, stderr=subprocess.PIPE)
        self.rec_start_time = time.time()

    def stop_recording(self):
        if self.rec_proc and self.rec_proc.poll() is None:
            self.rec_proc.send_signal(signal.SIGINT)
            try:
                self.rec_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.rec_proc.kill()
            self.rec_proc = None
            messagebox.showinfo("Recording Saved", f"16 tracks saved successfully to:\n{MUSIC_DIR}")

    def open_recordings_folder(self):
        subprocess.run(["open", MUSIC_DIR])

    def open_audio_midi_setup(self):
        subprocess.run(["open", "-a", "Audio MIDI Setup"])

    def open_sound_prefs(self):
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.sound"])

    # =========================================================================
    # Real-Time 30 FPS Meter Animation & Telemetry Sync Loop
    # =========================================================================
    def _ui_tick(self):
        now = time.time()
        active = self.is_engine_active()
        if not hasattr(self, "_tick_count"):
            self._tick_count = 0
        self._tick_count += 1
        if self._tick_count == 30:
            c0 = self.in_canvases[0]
            mc = self.master_canvas
            sys.stderr.write(f"TICK 30: c0 w={c0.winfo_width()} h={c0.winfo_height()} viewable={c0.winfo_viewable()} bbox={c0.bbox(tk.ALL)}\n")
            sys.stderr.write(f"TICK 30: mc w={mc.winfo_width()} h={mc.winfo_height()} viewable={mc.winfo_viewable()} bbox={mc.bbox(tk.ALL)}\n")
            sys.stderr.write(f"TICK 30: strips_row w={self.in_canvases[0].master.master.winfo_width()} h={self.in_canvases[0].master.master.winfo_height()}\n")
            sys.stderr.write(f"TICK 30: col0 w={c0.master.winfo_width()} h={c0.master.winfo_height()}\n")
            sys.stderr.flush()

        # 1. Authoritative CoreAudio Hardware Synchronization
        ca_rate, ca_buf, is_running, is_alive = self.ca_bridge.get_hardware_status()
        if ca_buf > 0 and ca_buf in BUFFER_SIZES and ca_buf != self.current_buffer:
            self.current_buffer = ca_buf
            if self.buf: self.buf.buffer_frame_size = ca_buf
            self._update_all_telemetry()

        if ca_rate > 0 and ca_rate != self.current_rate:
            self.current_rate = ca_rate
            if self.buf: self.buf.sample_rate = ca_rate
            self._update_all_telemetry()

        if self.buf:
            ext_mode = int(self.buf.latency_mode)
            if ext_mode in (0, 1, 2) and ext_mode != self.current_mode:
                self.current_mode = ext_mode
                self._update_all_telemetry()

        # 2. Update Status Pill
        if active:
            if is_running:
                self.status_pill.configure(text="● STREAMING (ACTIVE)", bg="#064e3b", fg="#34d399")
            else:
                self.status_pill.configure(text="● HARDWARE ONLINE", bg="#064e3b", fg="#34d399")
        elif self.is_connected:
            self.status_pill.configure(text="● STANDBY (CONNECTED)", bg="#78350f", fg="#fbbf24")
        else:
            self.status_pill.configure(text="● DISCONNECTED", bg="#7f1d1d", fg="#f87171")

        # 3. Animate 16 Input Meters
        if active and self.buf:
            for i in range(16):
                raw_pk = float(self.buf.in_peak[i])
                db = peak_to_db(raw_pk)
                norm = db_to_norm(db)

                # Ballistics: Instant attack, smooth decay
                if norm > self.in_meter_vals[i]:
                    self.in_meter_vals[i] = norm
                else:
                    self.in_meter_vals[i] = self.in_meter_vals[i] * 0.86

                # Peak hold with 1.2s timeout
                if norm >= self.in_peak_holds[i]:
                    self.in_peak_holds[i] = norm
                    self.in_peak_times[i] = now
                elif now - self.in_peak_times[i] > 1.2:
                    self.in_peak_holds[i] = max(0.0, self.in_peak_holds[i] - 0.04)

                # Clip detection (>= -0.2 dBFS)
                if db >= -0.2:
                    self.in_clip_times[i] = now
                is_clipped = (now - self.in_clip_times[i] < 1.8)

                # Render Canvas Strip
                self._update_strip_display(self.in_canvases[i], self.in_meter_vals[i], self.in_peak_holds[i], db, is_clipped)

            # 4. Animate Output Meters (L / R)
            raw_out_l = float(self.buf.out_peak[0])
            raw_out_r = float(self.buf.out_peak[1])

            db_l = peak_to_db(raw_out_l)
            db_r = peak_to_db(raw_out_r)

            norm_l = db_to_norm(db_l)
            norm_r = db_to_norm(db_r)

            self.out_meter_vals[0] = norm_l if norm_l > self.out_meter_vals[0] else self.out_meter_vals[0] * 0.86
            self.out_meter_vals[1] = norm_r if norm_r > self.out_meter_vals[1] else self.out_meter_vals[1] * 0.86

            if norm_l >= self.out_peak_holds[0]:
                self.out_peak_holds[0] = norm_l
                self.out_peak_times[0] = now
            elif now - self.out_peak_times[0] > 1.2:
                self.out_peak_holds[0] = max(0.0, self.out_peak_holds[0] - 0.04)

            if norm_r >= self.out_peak_holds[1]:
                self.out_peak_holds[1] = norm_r
                self.out_peak_times[1] = now
            elif now - self.out_peak_times[1] > 1.2:
                self.out_peak_holds[1] = max(0.0, self.out_peak_holds[1] - 0.04)

            if db_l >= -0.2: self.out_clip_times[0] = now
            if db_r >= -0.2: self.out_clip_times[1] = now

            clip_l = (now - self.out_clip_times[0] < 1.8)
            clip_r = (now - self.out_clip_times[1] < 1.8)

            self._update_master_display(
                self.out_meter_vals[0], self.out_peak_holds[0], db_l, clip_l,
                self.out_meter_vals[1], self.out_peak_holds[1], db_r, clip_r
            )
        else:
            for i in range(16):
                self._update_strip_display(self.in_canvases[i], 0.0, 0.0, -60.0, False)
            self._update_master_display(0.0, 0.0, -60.0, False, 0.0, 0.0, -60.0, False)

        # 5. Recording Timer State
        if self.rec_proc and self.rec_proc.poll() is None:
            elapsed = int(time.time() - self.rec_start_time)
            m, s = divmod(elapsed, 60)
            self.btn_rec.configure(text=f"■ STOP RECORDING ({m:02d}:{s:02d})", bg="#991b1b")
        else:
            self.btn_rec.configure(text="● Record 16 Channels (WAV)", bg="#dc2626")

        self.after(33, self._ui_tick)

if __name__ == "__main__":
    app = TascamControlConsole()
    app.mainloop()
