# TASCAM US-1800 Native Driver Suite (macOS Apple Silicon & Linux)

<p align="center">
  <img src="https://img.shields.io/badge/Platform-macOS%20Apple%20Silicon%20(M1--M4)%20%7C%20Intel-black?style=for-the-badge&logo=apple" alt="Platform" />
  <img src="https://img.shields.io/badge/Architecture-CoreAudio%20HAL%20Plugin%20%2B%20IOKit-blue?style=for-the-badge" alt="Architecture" />
  <img src="https://img.shields.io/badge/Latency-Ultra--Low%20(~5.5ms%20RTL)-success?style=for-the-badge" alt="Latency" />
  <img src="https://img.shields.io/badge/Channels-16%20In%20%2F%204%20Out-orange?style=for-the-badge" alt="Channels" />
  <img src="https://img.shields.io/badge/SIP-Enabled%20(No%20Kext%20Required)-brightgreen?style=for-the-badge" alt="SIP" />
</p>

<p align="center">
  <img src="docs/console_preview.png" alt="TASCAM US-1800 Pro Audio Control Console" width="880" style="border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.5);" />
</p>

---

## О проекте / Overview

**TASCAM US-1800** — легендарный рэковый 16-входовой / 4-выходной USB 2.0 аудиоинтерфейс с великолепными преампами. Официальные драйверы от производителя перестали работать на современных версиях macOS из-за отказа Apple от устаревших расширений ядра (`.kext`) и перехода на процессоры Apple Silicon (M1/M2/M3/M4).

Данный проект — это **полнофункциональный нативный драйвер нового поколения**, написанный с нуля на C и Objective-C на основе реверс-инжиниринга протокола обмена. Драйвер работает целиком в пространстве пользователя (userspace) через **Apple IOKit** и **CoreAudio AudioServerPlugIn HAL**, **не требует отключения SIP** (System Integrity Protection) и обеспечивает профессиональную студийную стабильность с минимальной аппаратной задержкой.

> **English summary:** Native userspace CoreAudio HAL driver and hardware streaming engine for the **TASCAM US-1800** USB audio interface on modern macOS (Apple Silicon M1–M4 & Intel, macOS 12 Monterey through macOS 15 Sequoia+). No kernel extensions, no SIP disabling required, 16 inputs / 4 outputs, hardware PLL clock sync, native AppKit control console, down to ~5.5 ms round-trip latency.

---

## Ключевые возможности

* **Полная нативная поддержка Apple Silicon (ARM64) и Intel (x86_64):**
  * macOS 12 Monterey, 13 Ventura, 14 Sonoma, 15 Sequoia и новее.
* **Прямая интеграция с macOS CoreAudio:**
  * Карта распознается как нативное системное аудиоустройство `TASCAM US-1800` во всех DAW (Logic Pro, Studio One, Reaper, Ableton Live, Cubase, FL Studio, Bitwig, Pro Tools) и системных настройках macOS.
* **16 Каналов захвата (Capture) и 4 Канала вывода (Playback):**
  * **Ch 1–8:** Микрофонные/линейные XLR входы на передней панели (с фантомным питанием +48V).
  * **Ch 9–10:** Инструментальные (Hi-Z гитара/бас) / линейные входы 1/4" на передней панели.
  * **Ch 11–14:** Балансные линейные входы 1/4" TRS на задней панели.
  * **Ch 15–16:** Цифровой коаксиальный вход S/PDIF (RCA).
  * **Out 1–2:** Основные мониторные выходы Main Outputs и выход на наушники Phones.
  * **Out 3–4:** Независимые линейные выходы Line Outputs 3 & 4.
* **Ультранизкая задержка (Ultra-Low Latency):**
  * Поддержка аппаратных буферов CoreAudio от **32 до 2048 сэмплов**.
  * Физическая задержка в обе стороны (Round-Trip Latency) **~5.5–6.0 мс** на буфере 32/64 при 44.1 кГц.
  * Три переключаемых на лету профиля движка: `Ultra-Low (Live)`, `Balanced (Studio)`, `Safe (Heavy Mix)`.
* **Аппаратная PLL-синхронизация частоты (Feedback Endpoint 0x81):**
  * Точная дробная подстройка фазового аккумулятора под кварцевый генератор ЦАПа в реальном времени. Полное отсутствие щелчков, треска и дрейфа питча.
* **100% Bit-Perfect 24-bit PCM:**
  * Прямая побитовая передача PCM сэмплов (`S24_3LE`) без потерь динамического диапазона.
* **CoreAudio HAL Grace Period (Защита от отвалов):**
  * Интеллектуальный 4-секундный демпфер предотвращает сброс звука на динамики ноутбука при микросекундных тайминговых задержках USB.
* **Устойчивость к анимациям и App Nap:**
  * Поток реального времени с системным приоритетом `QOS_CLASS_USER_INTERACTIVE` и очередью в 12 мс — воспроизведение не заикается при переключении между рабочими столами (Spaces) и Mission Control.
* **Нативная панель управления (Apple Silicon Pro Console):**
  * Приложение **`TASCAM US-1800.app`** на Cocoa/AppKit с 16-канальным LED-мостом, индикацией перегрузки (Clip), dBFS-измерителями, переключением буферов и встроенным генератором тестового тона 440 Гц.
* **Поддержка Linux:**
  * В репозитории также включен исходный код модуля ядра Linux ALSA (`tascam-us1800/linux-driver/`).

---

## Быстрая установка (macOS)

### Способ 1: В один клик (Рекомендуется)

1. Подключите звуковую карту **TASCAM US-1800** к Mac через USB.
2. Дважды щелкните по файлу **`Установить_драйвер.command`** (или **`Install_Driver.command`**).
3. В открывшемся окне Терминала введите ваш пароль администратора Mac.
4. Скрипт соберет актуальные бинарники под ваш процессор, установит плагин HAL, запустит фоновую службу и откроет «Настройки звука».
5. Выберите **TASCAM US-1800** в качестве устройства вывода и ввода!

### Способ 2: Через Терминал

```bash
git clone https://github.com/yuracopper/us-1800-mac.git
cd us-1800-mac/tascam-us1800/mac-driver
sudo ./install_hal_driver.sh
```

### Запуск панели управления

Дважды щелкните на **`TASCAM US-1800.app`** на Рабочем столе или в корне репозитория. В панели доступны:
* Выбор аппаратного размера буфера (32, 64, 128, 256, 512, 1024, 2048 сэмплов);
* Переключение профиля задержки (`Ultra-Low`, `Balanced`, `Safe`);
* Мониторинг всех 16 входов и 2 мастер-каналов;
* Тестовый генератор синусоиды 440 Гц для проверки тракта;
* Быстрый переход в системные настройки Audio MIDI Setup и macOS Sound.

---

## Архитектура драйвера

```
  ┌────────────────────────────────────────────────────────┐
  │        DAW Applications (Logic, Studio One, etc.)       │
  └───────────────────────────┬────────────────────────────┘
                              │ CoreAudio API (Float32 PCM)
                              ▼
  ┌────────────────────────────────────────────────────────┐
  │  TASCAM_US1800.driver (CoreAudio HAL Plugin in coreaudiod) │
  │  - ZeroTimeStamp tracking                              │
  │  - 4.0s Connection Grace Period                        │
  │  - Latency profile & buffer negotiation                │
  └───────────────────────────┬────────────────────────────┘
                              │ Lock-Free Shared Memory Ring (/tascam_us1800_shm)
                              ▼
  ┌────────────────────────────────────────────────────────┐
  │       tascam_live_engine (Userspace Real-Time Daemon)   │
  │  - Mach Real-Time Priority (QoS USER_INTERACTIVE)       │
  │  - Fractional phase PLL sync via EP 0x81               │
  │  - 16-channel bit-slice decoder                        │
  │  - 12ms Isochronous transfer queue with auto-resync    │
  └───────────────────────────┬────────────────────────────┘
                              │ Apple IOKit USB (EP 0x02, EP 0x86, EP 0x81)
                              ▼
  ┌────────────────────────────────────────────────────────┐
  │              TASCAM US-1800 USB Audio Device           │
  └────────────────────────────────────────────────────────┘
```

---

## Структура репозитория

```text
├── Install_Driver.command               # Скрипт установки в 1 клик (English)
├── Установить_драйвер.command           # Скрипт установки в 1 клик (Русский)
├── TASCAM US-1800.app                   # Нативное приложение Pro Audio Control Console
├── docs/
│   └── console_preview.png              # Скриншот панели управления
└── tascam-us1800/
    ├── mac-driver/                      # Нативный драйвер и компоненты для macOS
    │   ├── tascam_live_engine.c         # Высокоскоростной аппаратный IOKit USB-движок
    │   ├── tascam_hal_plugin.c          # CoreAudio AudioServerPlugIn HAL драйвер
    │   ├── tascam_console_native.m      # Исходный код нативной панели на AppKit
    │   ├── tascam_shm.h                 # Заголовочный файл lock-free разделяемой памяти
    │   ├── build.sh                     # Скрипт компиляции всех бинарников под ARM64
    │   ├── install_hal_driver.sh        # Скрипт системной установки и настройки LaunchDaemon
    │   ├── monitor.c                    # Диагностическая утилита джиттера и дрейфа клока
    │   └── TASCAM_US1800.driver/        # Собранный бандл CoreAudio HAL драйвера
    └── linux-driver/                    # Драйвер ALSA для ядра Linux
        ├── us1800.c                     # Регистрация устройства и ALSA интерфейсов
        ├── us1800_playback.c            # Вывод звука с изохронным фидбеком
        ├── us1800_capture.c             # Захват 16 каналов через Bulk EP 0x86
        └── Makefile                     # Сборка модуля ядра Linux
```

---

## English Quick Start

1. Connect the **TASCAM US-1800** to your Mac via USB.
2. Double-click **`Install_Driver.command`** and enter your administrator password in Terminal.
3. Open **System Settings > Sound** and select **TASCAM US-1800** as output and input.
4. Launch **`TASCAM US-1800.app`** to configure buffer sizes, monitor real-time input levels, and switch latency profiles.

---

## Требования / Requirements

* **macOS:** 12.0 (Monterey), 13.0 (Ventura), 14.0 (Sonoma), 15.0 (Sequoia) или новее.
* **Процессор:** Apple Silicon (M1, M1 Pro/Max/Ultra, M2, M3, M4) или Intel Core i5/i7/i9/Xeon (x86_64).
* **SIP:** Включен (отключать System Integrity Protection **не требуется**).
* **Кабель:** USB-кабель со стабильным питанием (желательно подключать напрямую в Mac или через качественный powered hub).

---

## Лицензия / License

Проект распространяется под лицензией **MIT / GPLv2**. Создано с любовью для музыкантов, звукорежиссеров и владельцев надежного оборудования TASCAM.
