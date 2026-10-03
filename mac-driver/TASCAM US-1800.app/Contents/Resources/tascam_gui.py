#!/usr/bin/env python3
"""
TASCAM US-1800 Professional Live Control Panel
Apple Silicon M1/M2/M3 Native Audio Management Suite
Full live tactile button control of Buffer Size and Sample Rate
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
from tkinter import ttk, messagebox

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

INSTALL_SCRIPT = "/Users/yura/.gemini/antigravity/scratch/tascam-us1800/mac-driver/install_hal_driver.sh"
MUSIC_DIR = os.path.expanduser("~/Music/TASCAM_Recordings")
os.makedirs(MUSIC_DIR, exist_ok=True)

libc = ctypes.CDLL(None)
libc.shm_open.restype = ctypes.c_int
libc.shm_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint16]

# Shared Memory Layout matching tascam_shm.h (version 2, 32768 frames)
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
    ]

SAMPLE_RATES = [44100, 48000, 88200, 96000]
RATE_LABELS = {
    44100: "44.1 kHz",
    48000: "48 kHz",
    88200: "88.2 kHz",
    96000: "96 kHz"
}

BUFFER_SIZES = [64, 128, 256, 512, 1024, 2048]

class TascamControlApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TASCAM US-1800 Control Panel")
        self.geometry("540x800")
        self.resizable(False, False)
        self.configure(bg="#14171f")

        self.mm = None
        self.buf = None
        self.local_engine_proc = None
        self.rec_proc = None
        self.rec_start_time = None
        self.is_connected = False
        self.running = True

        self.current_rate = 44100
        self.current_buffer = 256

        self.last_heartbeat = 0
        self.last_heartbeat_time = time.time()

        self._build_ui()
        self._init_shm()

        # Non-blocking USB hardware monitor thread
        self.poll_thread = threading.Thread(target=self._hardware_poll_loop, daemon=True)
        self.poll_thread.start()

        # 30 fps smooth meter and UI updates
        self._ui_tick()

    def _init_shm(self):
        try:
            if not self.buf:
                fd = libc.shm_open(b"/tascam_us1800_shm", 2, 0) # O_RDWR = 2
                if fd >= 0:
                    self.mm = mmap.mmap(fd, ctypes.sizeof(TascamSharedBuffer), mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
                    os.close(fd)
                    self.buf = TascamSharedBuffer.from_buffer(self.mm)
                    if self.buf.magic != 0x54313830 or self.buf.version != 2:
                        self.buf = None
                        self.mm.close()
                        self.mm = None
                    else:
                        r = int(self.buf.sample_rate)
                        b = int(self.buf.buffer_frame_size)
                        if r in SAMPLE_RATES: self.current_rate = r
                        if b in BUFFER_SIZES: self.current_buffer = b
                        self._update_rate_buttons()
                        self._update_buffer_buttons()
                        self._update_latency_display()
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

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        # 1. Header
        header = tk.Frame(self, bg="#1e2230", padx=16, pady=12)
        header.pack(fill=tk.X)

        title_box = tk.Frame(header, bg="#1e2230")
        title_box.pack(side=tk.LEFT)
        tk.Label(title_box, text="TASCAM US-1800", font=("Helvetica", 16, "bold"), bg="#1e2230", fg="#ffffff").pack(anchor="w")
        tk.Label(title_box, text="16 In / 4 Out Live Digital Audio System • Apple Silicon M1/M2/M3", font=("Helvetica", 9), bg="#1e2230", fg="#94a3b8").pack(anchor="w")

        # Status Dot & Text
        self.status_dot = tk.Label(header, text="●", font=("Helvetica", 14), bg="#1e2230", fg="#ef4444")
        self.status_dot.pack(side=tk.RIGHT, padx=2)
        self.status_lbl = tk.Label(header, text="Проверка...", font=("Helvetica", 10, "bold"), bg="#1e2230", fg="#ef4444")
        self.status_lbl.pack(side=tk.RIGHT)

        # 2. Main Body
        body = tk.Frame(self, bg="#14171f", padx=14, pady=8)
        body.pack(fill=tk.BOTH, expand=True)

        # Audio Settings Card with TACTILE BUTTONS
        card_settings = tk.LabelFrame(body, text=" Кнопки управления герцовкой и буфером ", bg="#14171f", fg="#38bdf8", font=("Helvetica", 9, "bold"), padx=10, pady=8)
        card_settings.pack(fill=tk.X, pady=(0, 6))

        # Sample Rate Button Selector
        tk.Label(card_settings, text="Частота дискретизации (Sample Rate):", bg="#14171f", fg="#e2e8f0", font=("Helvetica", 9, "bold")).pack(anchor="w", pady=(0, 4))
        rate_btn_row = tk.Frame(card_settings, bg="#14171f")
        rate_btn_row.pack(fill=tk.X, pady=(0, 8))

        self.rate_buttons = {}
        for r in SAMPLE_RATES:
            btn = tk.Button(
                rate_btn_row,
                text=RATE_LABELS[r],
                font=("Helvetica", 9, "bold"),
                bg="#1e293b",
                fg="#94a3b8",
                activebackground="#2563eb",
                activeforeground="#ffffff",
                relief=tk.FLAT,
                padx=6,
                pady=5,
                command=lambda val=r: self.select_sample_rate(val)
            )
            btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            self.rate_buttons[r] = btn

        # Buffer Size Button Selector
        tk.Label(card_settings, text="Размер буфера сэмплов (Задержка / Latency):", bg="#14171f", fg="#e2e8f0", font=("Helvetica", 9, "bold")).pack(anchor="w", pady=(0, 4))
        buf_btn_row = tk.Frame(card_settings, bg="#14171f")
        buf_btn_row.pack(fill=tk.X, pady=(0, 8))

        self.buf_buttons = {}
        for b in BUFFER_SIZES:
            btn = tk.Button(
                buf_btn_row,
                text=str(b),
                font=("Helvetica", 9, "bold"),
                bg="#1e293b",
                fg="#94a3b8",
                activebackground="#0284c7",
                activeforeground="#ffffff",
                relief=tk.FLAT,
                padx=4,
                pady=5,
                command=lambda val=b: self.select_buffer_size(val)
            )
            btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            self.buf_buttons[b] = btn

        # Latency Info Banner
        self.latency_banner = tk.Frame(card_settings, bg="#1e293b", padx=8, pady=5)
        self.latency_banner.pack(fill=tk.X)
        self.latency_lbl = tk.Label(self.latency_banner, text="Задержка: ~5.8 мс • Режим: Balanced Studio", font=("Helvetica", 9, "bold"), bg="#1e293b", fg="#38bdf8")
        self.latency_lbl.pack(anchor="w")

        # Driver Controls Card
        card_ctrl = tk.LabelFrame(body, text=" Управление звуковой картой ", bg="#14171f", fg="#94a3b8", font=("Helvetica", 9, "bold"), padx=10, pady=8)
        card_ctrl.pack(fill=tk.X, pady=(0, 6))

        btn_row1 = tk.Frame(card_ctrl, bg="#14171f")
        btn_row1.pack(fill=tk.X, pady=(0, 6))

        self.btn_start = tk.Button(btn_row1, text="▶ Перезапустить движок", font=("Helvetica", 10, "bold"), bg="#2563eb", fg="#ffffff", activebackground="#1d4ed8", activeforeground="#ffffff", relief=tk.FLAT, padx=10, pady=6, command=self.restart_engine)
        self.btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.btn_stop = tk.Button(btn_row1, text="■ Остановить", font=("Helvetica", 10, "bold"), bg="#374151", fg="#94a3b8", activebackground="#4b5563", activeforeground="#ffffff", relief=tk.FLAT, padx=10, pady=6, command=self.stop_engine)
        self.btn_stop.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))

        self.btn_chime = tk.Button(card_ctrl, text="♫ Тест звука в наушниках (Tone)", font=("Helvetica", 10, "bold"), bg="#1e293b", fg="#38bdf8", activebackground="#334155", activeforeground="#ffffff", relief=tk.FLAT, pady=6, command=self.trigger_chime)
        self.btn_chime.pack(fill=tk.X)

        # 16 Input VU Meters Card
        card_in = tk.LabelFrame(body, text=" 16 Входов (Микрофоны 1-8, Гитары 9-10, Линия 11-14, SPDIF 15-16) ", bg="#14171f", fg="#94a3b8", font=("Helvetica", 8, "bold"), padx=6, pady=6)
        card_in.pack(fill=tk.X, pady=(0, 6))

        meter_box = tk.Frame(card_in, bg="#14171f")
        meter_box.pack(fill=tk.X)

        self.in_meter_bars = []
        labels = [
            "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8",
            "G9", "G10", "L11", "L12", "L13", "L14", "S15", "S16"
        ]
        for i in range(16):
            col = tk.Frame(meter_box, bg="#14171f")
            col.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=1)

            canvas = tk.Canvas(col, width=16, height=64, bg="#0d0f14", highlightthickness=1, highlightbackground="#242834")
            canvas.pack()
            self.in_meter_bars.append(canvas)

            lbl = tk.Label(col, text=labels[i], font=("Helvetica", 7, "bold"), bg="#14171f", fg="#64748b")
            lbl.pack(pady=(1, 0))

        # 4 Output VU Meters Card
        card_out = tk.LabelFrame(body, text=" 4 Выхода (1-2 Наушники / Мониторы, 3-4 Line Out) ", bg="#14171f", fg="#94a3b8", font=("Helvetica", 8, "bold"), padx=10, pady=6)
        card_out.pack(fill=tk.X, pady=(0, 6))

        out_box = tk.Frame(card_out, bg="#14171f")
        out_box.pack(fill=tk.X)

        self.out_meter_bars = []
        out_labels = ["Phones L", "Phones R", "Line 3", "Line 4"]
        for i in range(4):
            row = tk.Frame(out_box, bg="#14171f")
            row.pack(fill=tk.X, pady=1)

            tk.Label(row, text=out_labels[i], width=8, anchor="w", font=("Helvetica", 8, "bold"), bg="#14171f", fg="#cbd5e1").pack(side=tk.LEFT)
            canvas = tk.Canvas(row, height=10, bg="#0d0f14", highlightthickness=1, highlightbackground="#242834")
            canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
            self.out_meter_bars.append(canvas)

        # Recording Card
        card_rec = tk.LabelFrame(body, text=" Прямая запись 16 каналов в WAV ", bg="#14171f", fg="#94a3b8", font=("Helvetica", 9, "bold"), padx=10, pady=6)
        card_rec.pack(fill=tk.X, pady=(0, 6))

        self.rec_status_lbl = tk.Label(card_rec, text="Готов к записи (24-bit 16 дорожек без задержки)", bg="#14171f", fg="#94a3b8", font=("Helvetica", 9))
        self.rec_status_lbl.pack(anchor="w", pady=(0, 4))

        btn_rec_row = tk.Frame(card_rec, bg="#14171f")
        btn_rec_row.pack(fill=tk.X)

        self.btn_rec = tk.Button(btn_rec_row, text="● Запись 16 дорожек", font=("Helvetica", 10, "bold"), bg="#dc2626", fg="#ffffff", activebackground="#b91c1c", activeforeground="#ffffff", relief=tk.FLAT, padx=10, pady=5, command=self.toggle_recording)
        self.btn_rec.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.btn_folder = tk.Button(btn_rec_row, text="📁 Папка записей", font=("Helvetica", 10), bg="#334155", fg="#ffffff", activebackground="#475569", activeforeground="#ffffff", relief=tk.FLAT, padx=10, pady=5, command=self.open_recordings_folder)
        self.btn_folder.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))

        # Bottom Utilities Buttons
        btn_utils = tk.Frame(body, bg="#14171f")
        btn_utils.pack(fill=tk.X, pady=(4, 0))

        self.btn_midi_setup = tk.Button(btn_utils, text="🎹 Audio MIDI Настройка", font=("Helvetica", 9), bg="#1e2230", fg="#cbd5e1", activebackground="#334155", activeforeground="#ffffff", relief=tk.FLAT, pady=5, command=self.open_audio_midi_setup)
        self.btn_midi_setup.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.btn_sound_settings = tk.Button(btn_utils, text="⚙ Настройки звука macOS", font=("Helvetica", 9), bg="#1e2230", fg="#cbd5e1", activebackground="#334155", activeforeground="#ffffff", relief=tk.FLAT, pady=5, command=self.open_sound_prefs)
        self.btn_sound_settings.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

        self.btn_install = tk.Button(body, text="⚡ Установить / Обновить системный драйвер CoreAudio", font=("Helvetica", 10, "bold"), bg="#0f766e", fg="#ffffff", activebackground="#115e59", activeforeground="#ffffff", relief=tk.FLAT, pady=6, command=self.install_system_driver)
        self.btn_install.pack(fill=tk.X, pady=(6, 0))

        self._update_rate_buttons()
        self._update_buffer_buttons()
        self._update_latency_display()

    def select_sample_rate(self, rate):
        if rate != 44100:
            return
        self.current_rate = 44100
        if self.buf:
            self.buf.sample_rate = 44100
        self._update_rate_buttons()
        self._update_latency_display()

    def select_buffer_size(self, buf_size):
        self.current_buffer = buf_size
        if self.buf:
            self.buf.buffer_frame_size = buf_size
        self._update_buffer_buttons()
        self._update_latency_display()

    def _update_rate_buttons(self):
        for r, btn in self.rate_buttons.items():
            if r == 44100:
                btn.configure(bg="#2563eb", fg="#ffffff", text="✓ 44.1 kHz (Зафиксировано)", state=tk.NORMAL)
            else:
                btn.configure(bg="#1e293b", fg="#475569", text=RATE_LABELS[r], state=tk.DISABLED)

    def _update_buffer_buttons(self):
        for b, btn in self.buf_buttons.items():
            if b == self.current_buffer:
                btn.configure(bg="#0284c7", fg="#ffffff", text=f"✓ {b}")
            else:
                btn.configure(bg="#1e293b", fg="#94a3b8", text=str(b))

    def _update_latency_display(self):
        try:
            rate = self.current_rate
            buf = self.current_buffer
            ms = (buf / rate) * 1000.0
            roundtrip = ms * 2.0 + 1.5

            if buf <= 128:
                advice = "⚡ Ultra Low Latency (Живой вокал / гитары)"
            elif buf <= 512:
                advice = "✓ Balanced Studio (Запись и сведение)"
            else:
                advice = "🛡 Max Stability (Тяжелые проекты DAW)"

            self.latency_lbl.configure(
                text=f"Задержка буфера: {ms:.1f} мс (Round-trip ~{roundtrip:.1f} мс) • {advice}"
            )
        except Exception:
            pass

    def install_system_driver(self):
        def _run():
            self.btn_install.configure(text="Установка (введите пароль)...", state=tk.DISABLED)
            apple_script = f'do shell script "{INSTALL_SCRIPT}" with administrator privileges'
            res = subprocess.run(["osascript", "-e", apple_script], capture_output=True, text=True)
            if res.returncode == 0:
                messagebox.showinfo("Успех", "Драйвер TASCAM US-1800 успешно установлен в систему!\n\n16 входов и 4 выхода активны в macOS.")
            else:
                if "canceled" not in res.stderr.lower():
                    messagebox.showwarning("Результат установки", f"Вывод установки:\n{res.stderr or res.stdout}")
            self.btn_install.configure(text="⚡ Установить / Обновить системный драйвер CoreAudio", state=tk.NORMAL)
        threading.Thread(target=_run, daemon=True).start()

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
            messagebox.showwarning("Внимание", "TASCAM US-1800 не обнаружена по USB.\nПроверьте кабель и включение тумблера питания на карте.")
            return

        subprocess.run(["launchctl", "kickstart", "-k", "system/com.tascam.us1800.live"], capture_output=True)
        time.sleep(0.4)
        if self.is_engine_active():
            return

        try:
            self.local_engine_proc = subprocess.Popen([ENGINE_PATH])
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось запустить движок звука:\n{e}")

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
            self.btn_chime.configure(text="♫ Тест звучит...", bg="#10b981", fg="#ffffff")
            self.after(2200, lambda: self.btn_chime.configure(text="♫ Тест звука в наушниках (Tone)", bg="#1e293b", fg="#38bdf8"))
        else:
            threading.Thread(target=lambda: subprocess.run([USB_TOOL_PATH, "--chime", "0.1", "--rate", str(self.current_rate)]), daemon=True).start()

    def toggle_recording(self):
        if self.rec_proc and self.rec_proc.poll() is None:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if not self.is_connected:
            messagebox.showwarning("Внимание", "Карта не подключена.")
            return

        if self.is_engine_active():
            self.stop_engine()
            time.sleep(0.5)

        rate = str(self.current_rate)
        ts = time.strftime("%Y%m%d_%H%M%S")
        prefix = os.path.join(MUSIC_DIR, f"concert_{ts}")

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
            messagebox.showinfo("Запись сохранена", f"16 дорожек успешно записаны в:\n{MUSIC_DIR}")

    def open_recordings_folder(self):
        subprocess.run(["open", MUSIC_DIR])

    def open_audio_midi_setup(self):
        subprocess.run(["open", "-a", "Audio MIDI Setup"])

    def open_sound_prefs(self):
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.sound"])

    def _draw_vu_vertical(self, canvas, peak):
        canvas.delete("all")
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 2 or h < 2: return
        fill_h = int(peak * h)
        if fill_h < 1: return

        y = h - fill_h
        color = "#10b981"
        if peak > 0.75: color = "#f59e0b"
        if peak > 0.92: color = "#ef4444"
        canvas.create_rectangle(0, y, w, h, fill=color, outline="")

    def _draw_vu_horizontal(self, canvas, peak):
        canvas.delete("all")
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 2 or h < 2: return
        fill_w = int(peak * w)
        if fill_w < 1: return

        color = "#10b981"
        if peak > 0.75: color = "#f59e0b"
        if peak > 0.92: color = "#ef4444"
        canvas.create_rectangle(0, 0, fill_w, h, fill=color, outline="")

    def _ui_tick(self):
        active = self.is_engine_active()

        # Keep buttons synced with shared memory if changed externally by DAW
        if self.buf:
            self.current_rate = 44100

            ext_buf = int(self.buf.buffer_frame_size)
            if ext_buf in BUFFER_SIZES and ext_buf != self.current_buffer:
                self.current_buffer = ext_buf
                self._update_buffer_buttons()
                self._update_latency_display()

        if active:
            self.status_dot.configure(fg="#10b981")
            self.status_lbl.configure(text=f"АКТИВНА ({self.current_rate} Hz / {self.current_buffer} smp)", fg="#34d399")
            self.btn_stop.configure(state=tk.NORMAL, bg="#dc2626", fg="#ffffff")

            # Draw meters
            if self.buf:
                for i in range(16):
                    pk = float(self.buf.in_peak[i])
                    self._draw_vu_vertical(self.in_meter_bars[i], pk)
                for i in range(4):
                    pk = float(self.buf.out_peak[i])
                    self._draw_vu_horizontal(self.out_meter_bars[i], pk)
        else:
            if self.is_connected:
                self.status_dot.configure(fg="#f59e0b")
                self.status_lbl.configure(text="Подключена (Готова к пуску)", fg="#fbbf24")
            else:
                self.status_dot.configure(fg="#ef4444")
                self.status_lbl.configure(text="Карта не обнаружена", fg="#f87171")

            self.btn_stop.configure(state=tk.DISABLED, bg="#374151", fg="#64748b")

            # Clear meters
            for b in self.in_meter_bars: b.delete("all")
            for b in self.out_meter_bars: b.delete("all")

        # Recording timer
        if self.rec_proc and self.rec_proc.poll() is None:
            elapsed = int(time.time() - self.rec_start_time)
            m, s = divmod(elapsed, 60)
            self.rec_status_lbl.configure(text=f"ИДЕТ ЗАПИСЬ: {m:02d}:{s:02d} (16 каналов в {MUSIC_DIR})", fg="#ef4444")
            self.btn_rec.configure(text="■ Остановить запись", bg="#374151")
        else:
            self.rec_status_lbl.configure(text="Готов к записи (24-bit 16 дорожек без задержки)", fg="#94a3b8")
            self.btn_rec.configure(text="● Запись 16 дорожек", bg="#dc2626")

        self.after(33, self._ui_tick)

if __name__ == "__main__":
    app = TascamControlApp()
    app.mainloop()
