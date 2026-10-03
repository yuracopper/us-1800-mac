#!/usr/bin/env python3
"""
TASCAM US-1800 Professional Live Control Panel
Apple Silicon M1/M2/M3/M4 Native Audio Management Suite
Full real-time hardware telemetry and live CoreAudio profile switching.
"""

import ctypes
import os
import signal
import subprocess
import sys
import threading
import time
import mmap
import tkinter as tk
from tkinter import messagebox

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
    ENGINE_PATH = "/Users/yura/.gemini/antigravity/scratch/tascam-us1800/mac-driver/tascam_live_engine"

USB_TOOL_PATH = os.path.join(SCRIPT_DIR, "tascam_usb")
if not os.path.exists(USB_TOOL_PATH):
    USB_TOOL_PATH = os.path.join(SCRIPT_DIR, "..", "Resources", "tascam_usb")
if not os.path.exists(USB_TOOL_PATH):
    USB_TOOL_PATH = "/usr/local/bin/tascam_usb"
if not os.path.exists(USB_TOOL_PATH):
    USB_TOOL_PATH = "/Users/yura/.gemini/antigravity/scratch/tascam-us1800/mac-driver/tascam_usb"

INSTALL_SCRIPT = os.path.join(SCRIPT_DIR, "install_hal_driver.sh")
if not os.path.exists(INSTALL_SCRIPT):
    INSTALL_SCRIPT = "/Users/yura/Desktop/tascam-us1800/mac-driver/install_hal_driver.sh"
if not os.path.exists(INSTALL_SCRIPT):
    INSTALL_SCRIPT = "/Users/yura/.gemini/antigravity/scratch/tascam-us1800/mac-driver/install_hal_driver.sh"

MUSIC_DIR = os.path.expanduser("~/Music/TASCAM_Recordings")
os.makedirs(MUSIC_DIR, exist_ok=True)

TASCAM_CONF_PATH = "/var/tmp/tascam_mode.conf"

# Latency Profiles
TASCAM_MODE_LOW_LATENCY = 0
TASCAM_MODE_BALANCED = 1
TASCAM_MODE_SAFE = 2

LATENCY_PROFILES = [
    {
        "id": TASCAM_MODE_LOW_LATENCY,
        "title": "⚡ Ultra-Low (Live)",
        "name": "Live (Ultra-Low)",
        "badge": "~3.5 ms RTL",
        "desc": "Direct low-latency monitoring for guitars & vocals. Safety offset: 16 smp, 2x buffer cushion.",
        "bg_active": "#16a34a",
        "fg_active": "#ffffff",
    },
    {
        "id": TASCAM_MODE_BALANCED,
        "title": "✓ Balanced (Studio)",
        "name": "Balanced (Studio)",
        "badge": "~7.0 ms RTL",
        "desc": "Optimal balance for multi-track recording & mixing. Safety offset: 32 smp, 4x buffer cushion.",
        "bg_active": "#0284c7",
        "fg_active": "#ffffff",
    },
    {
        "id": TASCAM_MODE_SAFE,
        "title": "🛡 Safe (Heavy Mix)",
        "name": "Safe (Heavy Mix)",
        "badge": "~46 ms RTL",
        "desc": "Maximum buffering cushion for heavy DAW arrangements and high CPU plugin loads.",
        "bg_active": "#d97706",
        "fg_active": "#ffffff",
    },
]

BUFFER_SIZES = [16, 32, 64, 128, 256, 512, 1024, 2048]

# -----------------------------------------------------------------------------
# CoreAudio CTypes Interface (Real-Time Hardware Control)
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
        addr = AudioObjectPropertyAddress(0x64657623, 0x676c6f62, 0) # 'dev#', 'glob', 0
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
            name_addr = AudioObjectPropertyAddress(0x6c6e616d, 0x676c6f62, 0) # 'lnam'
            if self.ca.AudioObjectGetPropertyData(dev_id, ctypes.byref(name_addr), 0, None, ctypes.byref(name_size), ctypes.byref(cf_str)) == 0 and cf_str.value:
                buf = ctypes.create_string_buffer(256)
                self.cf.CFStringGetCString(cf_str, buf, 256, 0x08000100) # UTF-8
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
            # Check if cached ID is still valid and alive
            is_alive = ctypes.c_uint32(0)
            sz = ctypes.c_uint32(ctypes.sizeof(is_alive))
            addr = AudioObjectPropertyAddress(0x6c69766e, 0x676c6f62, 0) # 'livn'
            if self.ca.AudioObjectGetPropertyData(self.cached_dev_id, ctypes.byref(addr), 0, None, ctypes.byref(sz), ctypes.byref(is_alive)) == 0 and is_alive.value == 1:
                return self.cached_dev_id
        # Re-scan if invalid
        self.cached_dev_id = self.find_tascam_device_id()
        return self.cached_dev_id

    def get_hardware_status(self):
        dev_id = self.get_device_id()
        if not dev_id:
            return 44100, 128, False, False

        # 1. Buffer Size ('fsiz')
        buf_size = ctypes.c_uint32(0)
        sz_size = ctypes.c_uint32(ctypes.sizeof(buf_size))
        buf_addr = AudioObjectPropertyAddress(0x6673697a, 0x676c6f62, 0)
        self.ca.AudioObjectGetPropertyData(dev_id, ctypes.byref(buf_addr), 0, None, ctypes.byref(sz_size), ctypes.byref(buf_size))

        # 2. Sample Rate ('nsrt')
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
# Main Application GUI Class (Compact, High-Contrast Cocoa Widgets)
# -----------------------------------------------------------------------------
class TascamControlApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TASCAM US-1800 Control Panel")
        self.geometry("620x575")
        self.resizable(False, False)
        self.configure(bg="#0f121a")

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

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self._init_shm()

        # Initial CoreAudio sync
        rate, buf, _, is_alive = self.ca_bridge.get_hardware_status()
        if rate > 0: self.current_rate = rate
        if buf in BUFFER_SIZES: self.current_buffer = buf
        self._update_all_displays()

        # Background USB hardware monitor thread
        self.poll_thread = threading.Thread(target=self._hardware_poll_loop, daemon=True)
        self.poll_thread.start()

        # 30 fps smooth meter and telemetry updates
        self._ui_tick()

    def on_close(self):
        self.running = False
        self.destroy()

    def _init_shm(self):
        try:
            if not self.buf:
                fd = libc.shm_open(b"/tascam_us1800_shm", 2, 0) # O_RDWR = 2
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
                        self._update_all_displays()
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

            time.sleep(1.0)

    # -------------------------------------------------------------------------
    # UI Construction (Spacious 620px Layout, Zero Clipping)
    # -------------------------------------------------------------------------
    def _build_ui(self):
        # 1. Header Bar
        header = tk.Frame(self, bg="#161b26", padx=14, pady=8, highlightbackground="#252d3d", highlightthickness=1)
        header.pack(fill=tk.X)

        title_btn = tk.Button(
            header,
            text="TASCAM US-1800  •  16-In / 4-Out Apple Silicon Native",
            font=("Helvetica", 12, "bold"),
            bg="#161b26",
            fg="#f8fafc",
            activebackground="#161b26",
            activeforeground="#f8fafc",
            relief=tk.FLAT,
            bd=0,
            command=self.open_sound_prefs
        )
        title_btn.pack(side=tk.LEFT)

        self.status_pill = tk.Button(
            header,
            text="● HARDWARE ONLINE",
            font=("Helvetica", 9, "bold"),
            bg="#064e3b",
            fg="#34d399",
            activebackground="#064e3b",
            activeforeground="#34d399",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=3,
            command=self.restart_engine
        )
        self.status_pill.pack(side=tk.RIGHT)

        # 2. Main Content Body
        body = tk.Frame(self, bg="#0f121a", padx=12, pady=6)
        body.pack(fill=tk.BOTH, expand=True)

        # ---------------------------------------------------------------------
        # Section 1: 4 Visible Telemetry Indicator Tiles (Cocoa NSButtonCell)
        # ---------------------------------------------------------------------
        telem_frame = tk.Frame(body, bg="#0f121a")
        telem_frame.pack(fill=tk.X, pady=(0, 6))

        tile_base = {
            "font": ("Helvetica", 10, "bold"),
            "bg": "#1c2333",
            "activebackground": "#252e42",
            "activeforeground": "#ffffff",
            "relief": tk.RIDGE,
            "bd": 1,
            "pady": 6,
        }

        # Tile 1: Sample Rate
        self.tile_rate = tk.Button(
            telem_frame,
            text="44.1 kHz\nSample Rate",
            fg="#38bdf8",
            command=self.open_audio_midi_setup,
            **tile_base
        )
        self.tile_rate.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)

        # Tile 2: Active Buffer
        self.tile_buf = tk.Button(
            telem_frame,
            text="128 smp (2.9 ms)\nActive Buffer",
            fg="#38bdf8",
            command=self._cycle_next_buffer,
            **tile_base
        )
        self.tile_buf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)

        # Tile 3: Est. Round-Trip Latency (RTL)
        self.tile_rtl = tk.Button(
            telem_frame,
            text="~3.5 ms RTL\nEst. Latency",
            fg="#34d399",
            command=self._cycle_next_mode,
            **tile_base
        )
        self.tile_rtl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)

        # Tile 4: Hardware Status
        self.tile_status = tk.Button(
            telem_frame,
            text="● ONLINE\nHardware Active",
            fg="#34d399",
            command=self.restart_engine,
            **tile_base
        )
        self.tile_status.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)

        # ---------------------------------------------------------------------
        # Real-Time Confirmation & Action Feedback Banner
        # ---------------------------------------------------------------------
        self.banner_feedback = tk.Button(
            body,
            text="✓ CoreAudio Active: Buffer = 128 smp (2.9 ms) | RTL: ~3.5 ms | Sample Rate: 44.1 kHz",
            font=("Helvetica", 9, "bold"),
            bg="#162032",
            fg="#38bdf8",
            activebackground="#1e293b",
            activeforeground="#38bdf8",
            relief=tk.FLAT,
            bd=0,
            pady=4,
            command=self.open_sound_prefs
        )
        self.banner_feedback.pack(fill=tk.X, pady=(0, 6))

        # ---------------------------------------------------------------------
        # Section 2: Latency Profile Selector (Engine Real-Time Cushion)
        # ---------------------------------------------------------------------
        card_mode = tk.Frame(body, bg="#161b26", highlightbackground="#252d3d", highlightthickness=1, padx=10, pady=6)
        card_mode.pack(fill=tk.X, pady=(0, 6))

        mode_hdr = tk.Frame(card_mode, bg="#161b26")
        mode_hdr.pack(fill=tk.X, pady=(0, 4))
        tk.Button(
            mode_hdr,
            text="LATENCY PROFILE (REAL-TIME ENGINE SWITCHING)",
            font=("Helvetica", 8, "bold"),
            bg="#161b26",
            fg="#94a3b8",
            relief=tk.FLAT,
            bd=0
        ).pack(side=tk.LEFT)

        mode_btn_row = tk.Frame(card_mode, bg="#161b26")
        mode_btn_row.pack(fill=tk.X, pady=(0, 3))

        self.mode_buttons = {}
        for prof in LATENCY_PROFILES:
            mid = prof["id"]
            btn = tk.Button(
                mode_btn_row,
                text=prof["title"],
                font=("Helvetica", 9, "bold"),
                bg="#1e2436",
                fg="#94a3b8",
                activebackground=prof["bg_active"],
                activeforeground="#ffffff",
                relief=tk.FLAT,
                padx=8,
                pady=5,
                command=lambda val=mid: self.select_latency_mode(val)
            )
            btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            self.mode_buttons[mid] = btn

        self.mode_desc_btn = tk.Button(
            card_mode,
            text="",
            font=("Helvetica", 8),
            bg="#161b26",
            fg="#cbd5e1",
            relief=tk.FLAT,
            bd=0,
            anchor="w",
            justify=tk.LEFT
        )
        self.mode_desc_btn.pack(fill=tk.X)

        # ---------------------------------------------------------------------
        # Section 3: Buffer Size Selector (CoreAudio I/O Samples)
        # ---------------------------------------------------------------------
        card_buf = tk.Frame(body, bg="#161b26", highlightbackground="#252d3d", highlightthickness=1, padx=10, pady=6)
        card_buf.pack(fill=tk.X, pady=(0, 6))

        buf_hdr = tk.Frame(card_buf, bg="#161b26")
        buf_hdr.pack(fill=tk.X, pady=(0, 4))
        tk.Button(
            buf_hdr,
            text="BUFFER SIZE (COREAUDIO SAMPLES)",
            font=("Helvetica", 8, "bold"),
            bg="#161b26",
            fg="#94a3b8",
            relief=tk.FLAT,
            bd=0
        ).pack(side=tk.LEFT)
        self.buf_tip_btn = tk.Button(
            buf_hdr,
            text="Synchronized with Studio One / DAWs",
            font=("Helvetica", 8),
            bg="#161b26",
            fg="#38bdf8",
            relief=tk.FLAT,
            bd=0
        )
        self.buf_tip_btn.pack(side=tk.RIGHT)

        buf_btn_row = tk.Frame(card_buf, bg="#161b26")
        buf_btn_row.pack(fill=tk.X)

        self.buf_buttons = {}
        for b in BUFFER_SIZES:
            btn = tk.Button(
                buf_btn_row,
                text=str(b),
                font=("Helvetica", 9, "bold"),
                bg="#1e2436",
                fg="#94a3b8",
                activebackground="#0284c7",
                activeforeground="#ffffff",
                relief=tk.FLAT,
                padx=4,
                pady=4,
                command=lambda val=b: self.select_buffer_size(val)
            )
            btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            self.buf_buttons[b] = btn

        # Helper note for Studio One & DAWs
        self.lbl_daw_note = tk.Button(
            card_buf,
            text="ℹ Note: You can switch buffers here or in Studio One (Preferences ➔ Audio Setup). Both stay in sync.",
            font=("Helvetica", 8),
            bg="#161b26",
            fg="#64748b",
            relief=tk.FLAT,
            bd=0,
            anchor="w"
        )
        self.lbl_daw_note.pack(fill=tk.X, pady=(4, 0))

        # ---------------------------------------------------------------------
        # Section 4: Hardware Level Meters (16 In / 4 Out)
        # ---------------------------------------------------------------------
        card_meters = tk.Frame(body, bg="#161b26", highlightbackground="#252d3d", highlightthickness=1, padx=10, pady=6)
        card_meters.pack(fill=tk.X, pady=(0, 6))

        meters_hdr = tk.Frame(card_meters, bg="#161b26")
        meters_hdr.pack(fill=tk.X, pady=(0, 3))
        tk.Button(
            meters_hdr,
            text="HARDWARE LEVEL METERS (dBFS)",
            font=("Helvetica", 8, "bold"),
            bg="#161b26",
            fg="#94a3b8",
            relief=tk.FLAT,
            bd=0
        ).pack(side=tk.LEFT)
        tk.Button(
            meters_hdr,
            text="16 In (1-8 Mics • 9-10 Inst • 11-14 Line • 15-16 SPDIF)",
            font=("Helvetica", 8),
            bg="#161b26",
            fg="#64748b",
            relief=tk.FLAT,
            bd=0
        ).pack(side=tk.RIGHT)

        meter_box = tk.Frame(card_meters, bg="#161b26")
        meter_box.pack(fill=tk.X, pady=(2, 4))

        self.in_meter_bars = []
        labels = [
            "1", "2", "3", "4", "5", "6", "7", "8",
            "9", "10", "11", "12", "13", "14", "15", "16"
        ]
        for i in range(16):
            col = tk.Frame(meter_box, bg="#161b26")
            col.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=1)

            canvas = tk.Canvas(col, width=18, height=26, bg="#0b0e17", highlightthickness=1, highlightbackground="#22283a")
            canvas.pack()
            self.in_meter_bars.append(canvas)
            self._draw_vu_slot_vertical(canvas)

            lbl = tk.Label(col, text=labels[i], font=("Helvetica", 6, "bold"), bg="#161b26", fg="#94a3b8")
            lbl.pack(pady=(1, 0))

        # Output Meters Strip
        out_box = tk.Frame(card_meters, bg="#161b26")
        out_box.pack(fill=tk.X, pady=(2, 0))

        self.out_meter_bars = []
        out_labels = ["Phones 1-2 (Main)", "Line Out 3-4"]
        for i in range(2):
            row = tk.Frame(out_box, bg="#161b26")
            row.pack(fill=tk.X, pady=1)

            tk.Label(row, text=out_labels[i], width=16, anchor="w", font=("Helvetica", 7, "bold"), bg="#161b26", fg="#cbd5e1").pack(side=tk.LEFT)
            canvas = tk.Canvas(row, height=8, bg="#0b0e17", highlightthickness=1, highlightbackground="#22283a")
            canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
            self.out_meter_bars.append(canvas)
            self._draw_vu_slot_horizontal(canvas)

        # ---------------------------------------------------------------------
        # Section 5: Engine Controls & Direct Recording
        # ---------------------------------------------------------------------
        card_ctrl = tk.Frame(body, bg="#161b26", highlightbackground="#252d3d", highlightthickness=1, padx=10, pady=6)
        card_ctrl.pack(fill=tk.X, pady=(0, 6))

        btn_row1 = tk.Frame(card_ctrl, bg="#161b26")
        btn_row1.pack(fill=tk.X, pady=(0, 4))

        self.btn_start = tk.Button(
            btn_row1,
            text="▶ Restart Engine",
            font=("Helvetica", 9, "bold"),
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            command=self.restart_engine
        )
        self.btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.btn_stop = tk.Button(
            btn_row1,
            text="■ Stop Engine",
            font=("Helvetica", 9, "bold"),
            bg="#334155",
            fg="#94a3b8",
            activebackground="#475569",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            command=self.stop_engine
        )
        self.btn_stop.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.btn_chime = tk.Button(
            btn_row1,
            text="♫ Tone Test (440 Hz)",
            font=("Helvetica", 9, "bold"),
            bg="#1e2436",
            fg="#38bdf8",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            command=self.trigger_chime
        )
        self.btn_chime.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

        # Row 2: 16-Channel Direct WAV Recording
        rec_row = tk.Frame(card_ctrl, bg="#161b26")
        rec_row.pack(fill=tk.X)

        self.btn_rec = tk.Button(
            rec_row,
            text="● Record 16 Channels (WAV)",
            font=("Helvetica", 9, "bold"),
            bg="#dc2626",
            fg="#ffffff",
            activebackground="#b91c1c",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            command=self.toggle_recording
        )
        self.btn_rec.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.btn_folder = tk.Button(
            rec_row,
            text="📁 Open Recordings Folder",
            font=("Helvetica", 9),
            bg="#1e2436",
            fg="#cbd5e1",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            command=self.open_recordings_folder
        )
        self.btn_folder.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

        # ---------------------------------------------------------------------
        # Bottom Utilities Bar
        # ---------------------------------------------------------------------
        btn_utils = tk.Frame(body, bg="#0f121a")
        btn_utils.pack(fill=tk.X, pady=(2, 0))

        self.btn_midi_setup = tk.Button(
            btn_utils,
            text="🎹 Audio MIDI Setup",
            font=("Helvetica", 8),
            bg="#161b26",
            fg="#94a3b8",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            pady=4,
            command=self.open_audio_midi_setup
        )
        self.btn_midi_setup.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.btn_sound_settings = tk.Button(
            btn_utils,
            text="⚙ Sound Settings",
            font=("Helvetica", 8),
            bg="#161b26",
            fg="#94a3b8",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            pady=4,
            command=self.open_sound_prefs
        )
        self.btn_sound_settings.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.btn_install = tk.Button(
            btn_utils,
            text="⚡ Reinstall Driver",
            font=("Helvetica", 8, "bold"),
            bg="#0f766e",
            fg="#ffffff",
            activebackground="#115e59",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            pady=4,
            command=self.install_system_driver
        )
        self.btn_install.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

    # -------------------------------------------------------------------------
    # Visual Meter Drawing
    # -------------------------------------------------------------------------
    def _draw_vu_slot_vertical(self, canvas):
        canvas.delete("all")
        w = canvas.winfo_width() or 18
        h = canvas.winfo_height() or 26
        for y in range(4, h, 4):
            canvas.create_line(1, y, w - 1, y, fill="#181e2e")

    def _draw_vu_slot_horizontal(self, canvas):
        canvas.delete("all")
        w = canvas.winfo_width() or 300
        h = canvas.winfo_height() or 8
        for x in range(20, w, 20):
            canvas.create_line(x, 1, x, h - 1, fill="#181e2e")

    def _draw_vu_vertical(self, canvas, peak):
        canvas.delete("all")
        w = canvas.winfo_width() or 18
        h = canvas.winfo_height() or 26
        for y_slot in range(4, h, 4):
            canvas.create_line(1, y_slot, w - 1, y_slot, fill="#181e2e")

        if peak < 0.005:
            return

        fill_h = int(peak * h)
        if fill_h < 2: fill_h = 2
        y = h - fill_h

        color = "#10b981"
        if peak > 0.70: color = "#f59e0b"
        if peak > 0.90: color = "#ef4444"
        canvas.create_rectangle(1, y, w - 1, h, fill=color, outline="")

    def _draw_vu_horizontal(self, canvas, peak):
        canvas.delete("all")
        w = canvas.winfo_width() or 300
        h = canvas.winfo_height() or 8
        for x_slot in range(20, w, 20):
            canvas.create_line(x_slot, 1, x_slot, h - 1, fill="#181e2e")

        if peak < 0.005:
            return

        fill_w = int(peak * w)
        if fill_w < 2: fill_w = 2

        color = "#10b981"
        if peak > 0.70: color = "#f59e0b"
        if peak > 0.90: color = "#ef4444"
        canvas.create_rectangle(1, 1, fill_w, h - 1, fill=color, outline="")

    # -------------------------------------------------------------------------
    # Actions & Real-Time Event Handlers
    # -------------------------------------------------------------------------
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

        # Persist mode across reboots
        try:
            with open(TASCAM_CONF_PATH, "w") as f:
                f.write(f"{mode}\n")
        except Exception:
            pass

        # Profile-informed buffer adjustments
        if mode == TASCAM_MODE_LOW_LATENCY and self.current_buffer > 128:
            self.select_buffer_size(128)
        elif mode == TASCAM_MODE_BALANCED and (self.current_buffer < 128 or self.current_buffer > 512):
            self.select_buffer_size(256)
        elif mode == TASCAM_MODE_SAFE and self.current_buffer < 512:
            self.select_buffer_size(512)
        else:
            self._update_all_displays()

    def select_buffer_size(self, buf_size):
        # 1. Update CoreAudio Hardware Device in real time
        ca_ok, _ = self.ca_bridge.set_hardware_buffer(buf_size)
        self.current_buffer = buf_size

        # 2. Update Shared Memory directly so live engine adapts immediately
        if self.buf:
            self.buf.buffer_frame_size = self.current_buffer

        # Immediate visual feedback
        self._update_all_displays(confirmed_buffer=self.current_buffer, ca_ok=ca_ok)

    def _update_all_displays(self, confirmed_buffer=None, ca_ok=True):
        # 1. Update Mode Buttons
        for prof in LATENCY_PROFILES:
            mid = prof["id"]
            btn = self.mode_buttons.get(mid)
            if not btn: continue
            if mid == self.current_mode:
                btn.configure(bg=prof["bg_active"], fg=prof["fg_active"], text=f"● {prof['title']}")
                self.mode_desc_btn.configure(text=f"➔ {prof['desc']} [{prof['badge']}]", fg="#e2e8f0")
            else:
                btn.configure(bg="#1e2436", fg="#94a3b8", text=prof["title"])

        # 2. Update Buffer Buttons
        for b, btn in self.buf_buttons.items():
            if b == self.current_buffer:
                btn.configure(bg="#0284c7", fg="#ffffff", text=f"✓ {b}")
            else:
                btn.configure(bg="#1e2436", fg="#94a3b8", text=str(b))

        # 3. Calculate Real-Time Latency Values
        rate = self.current_rate or 44100
        buf = self.current_buffer or 128
        ms = (buf / rate) * 1000.0

        if self.current_mode == TASCAM_MODE_LOW_LATENCY:
            safety = 16
            rtl = (buf * 2.0 / rate * 1000.0) + (safety / rate * 1000.0) + 1.2
            m_color = "#34d399"
        elif self.current_mode == TASCAM_MODE_BALANCED:
            safety = 32
            rtl = (buf * 3.0 / rate * 1000.0) + (safety / rate * 1000.0) + 2.0
            m_color = "#38bdf8"
        else:
            safety = 128
            rtl = (buf * 4.0 / rate * 1000.0) + (safety / rate * 1000.0) + 4.0
            m_color = "#fbbf24"

        # 4. Update the 4 High-Contrast Indicator Tiles
        self.tile_rate.configure(text=f"{rate / 1000.0:.1f} kHz\nSample Rate")
        self.tile_buf.configure(text=f"{buf} smp ({ms:.1f} ms)\nActive Buffer")
        self.tile_rtl.configure(text=f"~{rtl:.1f} ms RTL\nEst. Latency", fg=m_color)

        if self.is_engine_active():
            self.tile_status.configure(text="● ONLINE\nHardware Active", fg="#34d399")
        elif self.is_connected:
            self.tile_status.configure(text="● STANDBY\nUSB Connected", fg="#fbbf24")
        else:
            self.tile_status.configure(text="● OFFLINE\nDisconnected", fg="#f87171")

        # 5. Update Banner Feedback
        if confirmed_buffer is not None:
            tag = "CoreAudio Confirmed" if ca_ok else "Applied"
            self.banner_feedback.configure(
                text=f"✓ {tag}: Buffer = {buf} smp ({ms:.2f} ms) | Latency = ~{rtl:.1f} ms RTL | Clock: {rate / 1000.0:.1f} kHz",
                fg="#38bdf8",
                bg="#1a2744"
            )
        else:
            self.banner_feedback.configure(
                text=f"✓ CoreAudio Active: Buffer = {buf} smp ({ms:.2f} ms) | RTL: ~{rtl:.1f} ms | Sample Rate: {rate / 1000.0:.1f} kHz",
                fg="#38bdf8",
                bg="#162032"
            )

    # -------------------------------------------------------------------------
    # Hardware & Engine Management
    # -------------------------------------------------------------------------
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

    def trigger_chime(self):
        if not self.is_engine_active():
            self.start_engine()
            time.sleep(0.5)

        if self.buf:
            self.buf.cmd_chime_test = 1
            self.btn_chime.configure(text="♫ Tone Playing...", bg="#10b981", fg="#ffffff")
            self.after(2200, lambda: self.btn_chime.configure(text="♫ Tone Test (440 Hz)", bg="#1e2436", fg="#38bdf8"))
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

    def install_system_driver(self):
        def _run():
            self.btn_install.configure(text="Installing (Enter password)...", state=tk.DISABLED)
            apple_script = f'do shell script "{INSTALL_SCRIPT}" with administrator privileges'
            res = subprocess.run(["osascript", "-e", apple_script], capture_output=True, text=True)
            if res.returncode == 0:
                messagebox.showinfo("Success", "TASCAM US-1800 CoreAudio HAL driver installed successfully!\n\n16 Inputs and 4 Outputs are active in macOS.")
            else:
                if "canceled" not in res.stderr.lower():
                    messagebox.showwarning("Installation Result", f"Installer Output:\n{res.stderr or res.stdout}")
            self.btn_install.configure(text="⚡ Reinstall Driver", state=tk.NORMAL)
        threading.Thread(target=_run, daemon=True).start()

    # -------------------------------------------------------------------------
    # Main 30 fps UI Tick (Meters & Hardware Telemetry Polling)
    # -------------------------------------------------------------------------
    def _ui_tick(self):
        active = self.is_engine_active()

        # Continuously monitor real CoreAudio hardware buffer and rate from macOS / DAW
        ca_rate, ca_buf, is_running, is_alive = self.ca_bridge.get_hardware_status()
        shm_buf = int(self.buf.buffer_frame_size) if self.buf else 0
        actual_buf = shm_buf if (shm_buf > 0) else ca_buf
        if actual_buf > 0 and actual_buf != self.current_buffer:
            self.current_buffer = actual_buf
            self._update_all_displays()

        shm_rate = int(self.buf.sample_rate) if self.buf else 0
        actual_rate = shm_rate if (shm_rate > 0) else ca_rate
        if actual_rate > 0 and actual_rate != self.current_rate:
            self.current_rate = actual_rate
            self._update_all_displays()

        if self.buf:
            ext_mode = int(self.buf.latency_mode)
            if ext_mode in (0, 1, 2) and ext_mode != self.current_mode:
                self.current_mode = ext_mode
                self._update_all_displays()

        # Update Status Pill
        if active:
            if is_running:
                self.status_pill.configure(text=" ● STREAMING (ACTIVE) ", bg="#064e3b", fg="#34d399")
            else:
                self.status_pill.configure(text=" ● HARDWARE ONLINE ", bg="#064e3b", fg="#34d399")
            self.btn_stop.configure(state=tk.NORMAL, bg="#dc2626", fg="#ffffff")

            # Draw meters
            if self.buf:
                for i in range(16):
                    pk = float(self.buf.in_peak[i])
                    self._draw_vu_vertical(self.in_meter_bars[i], pk)
                # 4 channels: 0,1 to out 1-2, 2,3 to line 3-4
                pk_out12 = max(float(self.buf.out_peak[0]), float(self.buf.out_peak[1]))
                pk_out34 = max(float(self.buf.out_peak[2]), float(self.buf.out_peak[3]))
                self._draw_vu_horizontal(self.out_meter_bars[0], pk_out12)
                self._draw_vu_horizontal(self.out_meter_bars[1], pk_out34)
        else:
            if self.is_connected:
                self.status_pill.configure(text=" ● STANDBY (CONNECTED) ", bg="#78350f", fg="#fbbf24")
            else:
                self.status_pill.configure(text=" ● DISCONNECTED ", bg="#7f1d1d", fg="#f87171")

            self.btn_stop.configure(state=tk.DISABLED, bg="#334155", fg="#64748b")

            for b in self.in_meter_bars: self._draw_vu_slot_vertical(b)
            for b in self.out_meter_bars: self._draw_vu_slot_horizontal(b)

        # Recording state
        if self.rec_proc and self.rec_proc.poll() is None:
            elapsed = int(time.time() - self.rec_start_time)
            m, s = divmod(elapsed, 60)
            self.banner_feedback.configure(text=f"● RECORDING IN PROGRESS: {m:02d}:{s:02d} (16 Tracks)", fg="#ef4444")
            self.btn_rec.configure(text="■ Stop Recording", bg="#334155")
        else:
            self.btn_rec.configure(text="● Record 16 Channels (WAV)", bg="#dc2626")

        self.after(33, self._ui_tick)

if __name__ == "__main__":
    app = TascamControlApp()
    app.mainloop()
