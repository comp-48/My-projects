"""
Фотоловушка + Видеорегистратор v7.1
Адаптивная версия с оптимизацией быстродействия и многопоточности.

ОПТИМИЗАЦИИ v7.0 (относительно v6.0):
  • [OPT-1]  cv2.setNumThreads() один раз глобально, без per-open
  • [OPT-2]  Двойная буферизация кадра (swap) — убран лишний copy()
  • [OPT-3]  Детекция движения вынесена в отдельный поток камеры
  • [OPT-4]  count_files_on_disk через os.scandir, TTL 5 c
  • [OPT-5]  Ограничение очереди миниатюр (семафор + лимит видимых)
  • [OPT-6]  Батч-drain в фото-сейвере
  • [OPT-7]  UI-рендер: Image.fromarray(..., mode="RGB") без лишних копий
  • [OPT-8]  Скан камер: cap.grab() вместо cap.read() (×3–5)
  • [OPT-9]  MOG2 на downscale до 640 px (×12–15 по CPU)
  • [OPT-10] Единый UI-heartbeat (один after вместо двух)
  • [OPT-11] Архивный таймер 66 мс (15 fps) вместо 100 мс
  • [OPT-12] Ленивый _process_log_queue (интервал 500 мс, drain)

НОВОЕ в v7.1:
  • Умные имена файлов: «Видео №1 — 03.10.2026 23.45.16.avi»
  • [OVERLAY] Наложение штампа CAM + дата + время на видео и фото:
      – «CAM #N» крупно слева вверху (жёлтый, на полупрозрачной плашке)
      – «● REC» справа вверху во время записи
      – «03.10.2026  23:45:16» снизу слева (белый на плашке)
    Масштаб шрифта адаптируется к ширине кадра (640p → 4K).

Зависимости:
  pip install opencv-python Pillow imageio imageio-ffmpeg numpy
  Опционально: pyaudio (для аудио), ffmpeg в PATH (для mux)
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import cv2
import numpy as np
from PIL import Image, ImageTk
import time
import os
import json
import threading
import queue
import sys
import math
import struct
import shutil
import logging
import logging.handlers
from datetime import datetime
from collections import OrderedDict
import platform
import wave
import subprocess
import tempfile
import ctypes
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, as_completed

# === [OPT-1] Глобальная настройка OpenCV ===
_CPU_COUNT = multiprocessing.cpu_count()
try:
    cv2.setNumThreads(max(2, min(4, _CPU_COUNT // 2)))
except Exception:
    pass
try:
    cv2.setUseOptimized(True)
except Exception:
    pass

# === Совместимость Pillow 10+ ===
try:
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    _LANCZOS = Image.LANCZOS

# === Опциональный pyaudio ===
AUDIO_AVAILABLE = False
pyaudio = None
try:
    import pyaudio
    AUDIO_AVAILABLE = True
except ImportError:
    print("[AUDIO] pyaudio не установлен — запись звука отключена")

# === Windows-специфичное ===
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

if IS_WINDOWS:
    try:
        import winsound
    except ImportError:
        winsound = None
else:
    winsound = None


# ============================================================
#                       ЛОГИРОВАНИЕ
# ============================================================
def setup_logging():
    log_dir = os.path.join(os.path.expanduser("~"), ".fotolovushka")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "fotolovushka.log")

    logger = logging.getLogger("fotolovushka")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(threadName)-15s] %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S")

    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=3,
        encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


log = setup_logging()


# ============================================================
#              ИНИЦИАЛИЗАЦИЯ БЭКЕНДА АРХИВА
# ============================================================
ARCHIVE_PLAYER_AVAILABLE = False
ARCHIVE_PLAYER_BACKEND = None
_iio = None
_iio_ffmpeg = None
_ffmpeg_exe = None
_ffprobe_exe = None

try:
    import imageio.v3 as _iio
    import imageio_ffmpeg as _iio_ffmpeg
    _ffmpeg_exe = _iio_ffmpeg.get_ffmpeg_exe()
    if _ffmpeg_exe and os.path.exists(_ffmpeg_exe):
        ARCHIVE_PLAYER_AVAILABLE = True
        ARCHIVE_PLAYER_BACKEND = "imageio"
        log.info(f"[ARCHIVE] imageio-ffmpeg OK: {_ffmpeg_exe}")
        _probe_candidate = _ffmpeg_exe.replace("ffmpeg", "ffprobe")
        if os.path.exists(_probe_candidate):
            _ffprobe_exe = _probe_candidate
    else:
        log.warning(f"[ARCHIVE] imageio-ffmpeg exe не найден: {_ffmpeg_exe}")
except Exception as _e:
    log.warning(f"[ARCHIVE] imageio-ffmpeg недоступен: {_e}")

if not ARCHIVE_PLAYER_AVAILABLE:
    try:
        _ff = shutil.which("ffmpeg")
        if _ff:
            ARCHIVE_PLAYER_AVAILABLE = True
            ARCHIVE_PLAYER_BACKEND = "ffmpeg-cli"
            _ffmpeg_exe = _ff
            _ffprobe_exe = shutil.which("ffprobe")
            log.info(f"[ARCHIVE] Используем системный ffmpeg: {_ff}")
    except Exception as _e:
        log.warning(f"[ARCHIVE] Проверка системного ffmpeg: {_e}")

if not _ffprobe_exe and _ffmpeg_exe:
    try:
        cand = _ffmpeg_exe.replace("ffmpeg", "ffprobe")
        if os.path.exists(cand):
            _ffprobe_exe = cand
    except Exception:
        pass

if not ARCHIVE_PLAYER_AVAILABLE:
    log.warning("[ARCHIVE] Плеер недоступен: ни imageio-ffmpeg, ни ffmpeg")


# ============================================================
#                       КОНСТАНТЫ
# ============================================================
APP_NAME = "Фотоловушка v7.1"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".fotolovushka.json")
VERSION = "7.1"

VIDEO_EXTS = (".avi", ".mp4", ".mov", ".mkv", ".mjpg")
PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".bmp")

STARTUP_LINK_NAME = "Фотоловушка.lnk"

MAX_CAMERAS_SCAN = 6
SCAN_WORKERS = 8
OPEN_WORKERS = 6
CAMERA_SCAN_TTL = 5.0
THUMB_WORKERS = 4
THUMB_CACHE_SIZE = 200
PREVIEW_CACHE_SIZE = 100
GRID_UPDATE_FPS = 15
SINGLE_UPDATE_FPS = 30
MAX_VISIBLE_THUMBS = 60          # [OPT-5]
COUNTERS_UPDATE_INTERVAL = 5.0   # [OPT-4]
MOTION_DOWNSCALE_WIDTH = 640     # [OPT-9]

SIGNAL_PHOTO_WAV_PATH = os.path.join(tempfile.gettempdir(),
                                     "fotolovushka_photo.wav")
SIGNAL_VIDEO_WAV_PATH = os.path.join(tempfile.gettempdir(),
                                     "fotolovushka_video.wav")

_SAMPLE_SIZE_CACHE = {}


# ============================================================
#                       LRU-КЭШ
# ============================================================
class LRUCache:
    """Потокобезопасный LRU-кэш."""
    def __init__(self, maxsize):
        self.maxsize = maxsize
        self._data = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return None

    def put(self, key, value):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def pop(self, key, default=None):
        with self._lock:
            return self._data.pop(key, default)

    def clear(self):
        with self._lock:
            self._data.clear()

    def __contains__(self, key):
        with self._lock:
            return key in self._data


_PHOTO_THUMB_CACHE = LRUCache(THUMB_CACHE_SIZE)
_VIDEO_PREVIEW_CACHE = LRUCache(PREVIEW_CACHE_SIZE)
_VIDEO_META_CACHE = LRUCache(50)


def _get_sample_size(fmt):
    if fmt in _SAMPLE_SIZE_CACHE:
        return _SAMPLE_SIZE_CACHE[fmt]
    if not AUDIO_AVAILABLE:
        _SAMPLE_SIZE_CACHE[fmt] = 2
        return 2
    p = None
    try:
        p = pyaudio.PyAudio()
        sz = p.get_sample_size(fmt)
    except Exception:
        sz = 2
    finally:
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass
    _SAMPLE_SIZE_CACHE[fmt] = sz
    return sz


# ============================================================
#      УНИВЕРСАЛЬНОЕ ЧТЕНИЕ ВИДЕО (с быстрым seek)
# ============================================================
def _probe_video_size(path):
    if _ffprobe_exe and os.path.exists(_ffprobe_exe):
        try:
            kwargs = {"capture_output": True, "text": True, "timeout": 10}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            r = subprocess.run(
                [_ffprobe_exe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=p=0:s=x", path], **kwargs)
            if r.returncode == 0:
                wh = r.stdout.strip().split("x")
                if len(wh) == 2:
                    return int(wh[0]), int(wh[1])
        except Exception as e:
            log.debug(f"ffprobe size error: {e}")
    if ARCHIVE_PLAYER_BACKEND == "imageio":
        try:
            with _iio.imopen(path, "r", plugin="FFMPEG") as reader:
                meta = reader.metadata()
                w = meta.get("width")
                h = meta.get("height")
                if w and h:
                    return int(w), int(h)
        except Exception as e:
            log.debug(f"imageio size error: {e}")
    return None, None


def _video_meta(path):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    cached = _VIDEO_META_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]

    fps = 30.0
    dur = 0.0

    if ARCHIVE_PLAYER_BACKEND == "imageio":
        try:
            meta = _iio.immeta(path, plugin="FFMPEG")
            fps = float(meta.get("fps", 30.0) or 30.0)
            d = meta.get("duration", 0)
            dur = float(d) if d else 0.0
        except Exception as e:
            log.debug(f"meta imageio error: {e}")
            try:
                meta = _iio.immeta(path)
                fps = float(meta.get("fps", 30.0) or 30.0)
                d = meta.get("duration", 0)
                dur = float(d) if d else 0.0
            except Exception as e2:
                log.debug(f"meta imageio (no plugin) error: {e2}")

    if dur <= 0 and _ffprobe_exe and os.path.exists(_ffprobe_exe):
        try:
            kwargs = {"capture_output": True, "text": True, "timeout": 10}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            r = subprocess.run(
                [_ffprobe_exe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=r_frame_rate",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                **kwargs)
            if r.returncode == 0 and "/" in r.stdout:
                num, den = r.stdout.strip().split("/")
                if float(den):
                    fps = float(num) / float(den)
            r2 = subprocess.run(
                [_ffprobe_exe, "-v", "error", "-show_entries",
                 "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                **kwargs)
            if r2.returncode == 0:
                try:
                    dur = float(r2.stdout.strip())
                except ValueError:
                    pass
        except Exception as e:
            log.debug(f"ffprobe meta error: {e}")

    if dur <= 0:
        try:
            sz = os.path.getsize(path)
            dur = max(0.1, sz / (1024 * 512))
        except Exception:
            dur = 0.0

    _VIDEO_META_CACHE.put(path, (mtime, fps, dur))
    return fps, dur


def _video_frames_iter(path, start_sec=0.0):
    if ARCHIVE_PLAYER_BACKEND == "imageio":
        if start_sec > 0.001:
            try:
                fps = 30.0
                try:
                    meta = _iio.immeta(path, plugin="FFMPEG")
                    fps = float(meta.get("fps", 30.0) or 30.0)
                except Exception:
                    pass
                start_frame = int(start_sec * fps)
                with _iio.imopen(path, "r", plugin="FFMPEG") as reader:
                    try:
                        reader.seek(start_frame, whence="start")
                    except Exception:
                        for _ in range(start_frame):
                            try:
                                next(reader)
                            except StopIteration:
                                break
                    for frame in reader:
                        if frame.ndim == 3 and frame.shape[2] == 3:
                            yield cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                        else:
                            yield frame
                    return
            except Exception as e:
                log.debug(f"[ARCHIVE] fast seek failed: {e}")

        try:
            skip = int(max(0.0, start_sec) * 30)
            idx = 0
            for frame in _iio.imiter(path, plugin="FFMPEG"):
                if idx < skip:
                    idx += 1
                    continue
                if frame.ndim == 3 and frame.shape[2] == 3:
                    yield cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                else:
                    yield frame
            return
        except Exception as e:
            log.debug(f"[ARCHIVE] imiter FFMPEG error: {e}")
            try:
                for frame in _iio.imiter(path):
                    if frame.ndim == 3 and frame.shape[2] == 3:
                        yield cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    else:
                        yield frame
                return
            except Exception as e2:
                log.debug(f"[ARCHIVE] imiter auto error: {e2}")
                return

    if ARCHIVE_PLAYER_BACKEND == "ffmpeg-cli":
        try:
            w, h = _probe_video_size(path)
            if not w or not h:
                return
            popen_kwargs = {"stdout": subprocess.PIPE,
                            "stderr": subprocess.DEVNULL,
                            "bufsize": 10 ** 7}
            if IS_WINDOWS:
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            cmd = [_ffmpeg_exe, "-loglevel", "error"]
            if start_sec > 0.001:
                cmd += ["-ss", f"{start_sec:.3f}"]
            cmd += ["-i", path, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
            proc = subprocess.Popen(cmd, **popen_kwargs)
            frame_size = w * h * 3
            try:
                while True:
                    raw = proc.stdout.read(frame_size)
                    if not raw or len(raw) < frame_size:
                        break
                    frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                        (h, w, 3))
                    yield frame
            finally:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass
        except Exception as e:
            log.debug(f"[ARCHIVE] ffmpeg-cli iter error: {e}")


def _get_first_frame(path):
    if ARCHIVE_PLAYER_BACKEND == "imageio":
        try:
            for frame in _iio.imiter(path, plugin="FFMPEG"):
                if frame.ndim == 3 and frame.shape[2] == 3:
                    return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                return frame
        except Exception as e:
            log.debug(f"[ARCHIVE] first frame imageio error: {e}")
    elif ARCHIVE_PLAYER_BACKEND == "ffmpeg-cli":
        try:
            for frame in _video_frames_iter(path):
                return frame
        except Exception as e:
            log.debug(f"[ARCHIVE] first frame cli error: {e}")
    return None


# ============================================================
#              ГЕНЕРАЦИЯ СИГНАЛЬНЫХ WAV
# ============================================================
def _generate_wav(path, tones, sample_rate=44100, amp=0.45, gap=0.06):
    try:
        frames = bytearray()
        amp_i = int(amp * 32767)

        def _tone(freq, dur):
            n = int(sample_rate * dur)
            fade = max(1, int(sample_rate * 0.02))
            for i in range(n):
                t = i / sample_rate
                if i < fade:
                    env = i / fade
                elif i > n - fade:
                    env = max(0.0, (n - i) / fade)
                else:
                    env = 1.0
                val = int(amp_i * env * math.sin(2 * math.pi * freq * t))
                frames.extend(struct.pack("<h", val))

        for idx, (freq, dur) in enumerate(tones):
            _tone(freq, dur)
            if idx < len(tones) - 1:
                silence = int(sample_rate * gap)
                for _ in range(silence):
                    frames.extend(struct.pack("<h", 0))

        wf = wave.open(path, "wb")
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))
        wf.close()
        return True
    except Exception as e:
        log.warning(f"Не удалось сгенерировать {path}: {e}")
        return False


def generate_photo_signal_wav(path=SIGNAL_PHOTO_WAV_PATH):
    return _generate_wav(path, [(880, 0.16), (1320, 0.20)])


def generate_video_signal_wav(path=SIGNAL_VIDEO_WAV_PATH):
    return _generate_wav(path, [(1200, 0.14), (900, 0.14), (1200, 0.20)])


# ============================================================
#              АВТОЗАПУСК
# ============================================================
def get_startup_folder():
    if not IS_WINDOWS:
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return os.path.join(appdata, "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup")


def is_autostart_enabled():
    if not IS_WINDOWS:
        return False
    startup = get_startup_folder()
    if not startup:
        return False
    return os.path.exists(os.path.join(startup, STARTUP_LINK_NAME))


def enable_autostart():
    if not IS_WINDOWS:
        return False, "Автозапуск поддерживается только в Windows"
    startup = get_startup_folder()
    if not startup:
        return False, "Не удалось найти папку автозагрузки"
    try:
        os.makedirs(startup, exist_ok=True)
        link_path = os.path.join(startup, STARTUP_LINK_NAME)
        if getattr(sys, "frozen", False):
            target = sys.executable
            arguments = ""
        else:
            python_exe = sys.executable
            pythonw = python_exe.replace("python.exe", "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = python_exe
            target = pythonw
            arguments = f'"{os.path.abspath(sys.argv[0])}"'
        if os.path.exists(link_path):
            os.remove(link_path)
        ps_script = (
            f'$ws = New-Object -ComObject WScript.Shell; '
            f'$sc = $ws.CreateShortcut("{link_path}"); '
            f'$sc.TargetPath = "{target}"; '
            f'$sc.Arguments = \'{arguments}\'; '
            f'$sc.WorkingDirectory = "{APP_DIR}"; '
            f'$sc.WindowStyle = 7; '
            f'$sc.Description = "Fotolovushka - autostart"; '
            f'$sc.Save()')
        kwargs = {"capture_output": True, "text": True, "timeout": 15}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script], **kwargs)
        if result.returncode != 0:
            return False, f"Ошибка PowerShell: {result.stderr.strip()}"
        return True, "Автозапуск включён"
    except Exception as e:
        return False, f"Ошибка: {e}"


def disable_autostart():
    if not IS_WINDOWS:
        return False, "Автозапуск поддерживается только в Windows"
    startup = get_startup_folder()
    if not startup:
        return False, "Не удалось найти папку автозагрузки"
    link_path = os.path.join(startup, STARTUP_LINK_NAME)
    try:
        if os.path.exists(link_path):
            os.remove(link_path)
            return True, "Автозапуск отключён"
        return True, "Автозапуск уже был отключён"
    except Exception as e:
        return False, f"Ошибка: {e}"


# ============================================================
#              ИМЕНА ФАЙЛОВ (УМНЫЕ)
# ============================================================
# Запрещённые в Windows символы в имени файла
_INVALID_FS_CHARS = '<>:"/\\|?*'


def _sanitize_name(name):
    """Заменяет недопустимые в имени файла символы на '_'."""
    result = []
    for ch in name:
        if ch in _INVALID_FS_CHARS or ord(ch) < 32:
            result.append('_')
        else:
            result.append(ch)
    return "".join(result).strip().rstrip('.')


def make_recording_name(kind, number, ext, cam_index=None):
    """
    Умные имена файлов:
      Видео    → «Видео №12 — 03.10.2026 23.45.16.avi»
      Фото     → «Фото №12 — 03.10.2026 23.45.16.jpg»
      Ручная   → «Запись №12 — 03.10.2026 23.45.16.avi»
      с cam-меткой: «Видео CAM0 №12 — 03.10.2026 23.45.16.avi»
    """
    now = datetime.now()
    date_part = now.strftime("%d.%m.%Y")
    time_part = now.strftime("%H.%M.%S")
    ms = now.microsecond // 1000

    prefix = {
        "Фото": "Фото",
        "Видео": "Видео",
        "Ручная": "Запись",
    }.get(kind, "Файл")

    cam_tag = ""
    if cam_index is not None:
        cam_tag = f" CAM{cam_index}"

    if kind == "Фото":
        name = (f"{prefix}{cam_tag} №{number} — "
                f"{date_part} {time_part}.{ms:03d}{ext}")
    else:
        name = f"{prefix}{cam_tag} №{number} — {date_part} {time_part}{ext}"

    return _sanitize_name(name)


# ============================================================
#                       ТЕМЫ
# ============================================================
THEMES = {
    "dark": {
        "BG_MAIN": "#0f1218", "BG_PANEL": "#171b24", "BG_CARD": "#1e2330",
        "BG_INPUT": "#252b3a", "BG_HOVER": "#2d3547",
        "FG_PRIMARY": "#e6e9ef", "FG_SECONDARY": "#8b93a7",
        "FG_MUTED": "#565e70",
        "ACCENT": "#4f8cff", "ACCENT_HOVER": "#6ba0ff",
        "SUCCESS": "#2ecc71", "SUCCESS_DARK": "#27ae60",
        "WARNING": "#f39c12",
        "DANGER": "#e74c3c", "DANGER_HOVER": "#ff5c4a",
        "DANGER_DARK": "#c0392b",
        "BORDER": "#2e3545", "DISABLED_BG": "#1a1f2a",
        "DISABLED_FG": "#454c5c", "SHADOW": "#080a0e",
        "TOAST_BG": "#252b3a", "LIST_SELECT": "#2c3a5c",
    },
    "light": {
        "BG_MAIN": "#f5f6f8", "BG_PANEL": "#ffffff", "BG_CARD": "#ffffff",
        "BG_INPUT": "#eff1f5", "BG_HOVER": "#e3e6ec",
        "FG_PRIMARY": "#1a1d23", "FG_SECONDARY": "#5a6070",
        "FG_MUTED": "#9aa0ae",
        "ACCENT": "#3a7bff", "ACCENT_HOVER": "#5a92ff",
        "SUCCESS": "#22a559", "SUCCESS_DARK": "#1c8a4a",
        "WARNING": "#e08c0b",
        "DANGER": "#d63a2f", "DANGER_HOVER": "#f04a3f",
        "DANGER_DARK": "#b02e24",
        "BORDER": "#d8dce3", "DISABLED_BG": "#e6e9ee",
        "DISABLED_FG": "#a0a6b2", "SHADOW": "#d0d4da",
        "TOAST_BG": "#1a1d23", "LIST_SELECT": "#d6e3ff",
    },
}


# ============================================================
#                    ВИДЖЕТЫ
# ============================================================
class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command=None, width=200, height=44,
                 radius=8, bg="#4f8cff", fg="#ffffff", hover_bg=None,
                 font=("Segoe UI", 10, "bold"), icon="", theme=None):
        super().__init__(parent, width=width, height=height,
                         bg=(theme or THEMES["dark"])["BG_CARD"],
                         highlightthickness=0, bd=0)
        self.command = command
        self.radius = radius
        self.bg_color = bg
        self.hover_color = hover_bg or self._lighten(bg)
        self.fg_color = fg
        self.font = font
        self.text = f"{icon}  {text}" if icon else text
        self._enabled = True
        self._hovering = False
        self._theme = theme or THEMES["dark"]
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _lighten(self, color):
        try:
            c = color.lstrip("#")
            r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
            r = min(255, int(r * 1.15))
            g = min(255, int(g * 1.15))
            b = min(255, int(b * 1.15))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return color

    def _rounded_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
                  x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
                  x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _draw(self):
        try:
            self.delete("all")
            w, h = int(self["width"]), int(self["height"])
            bg = self.bg_color
            if not self._enabled:
                bg = self._theme["DISABLED_BG"]
            elif self._hovering:
                bg = self.hover_color
            self._rounded_rect(0, 0, w - 1, h - 1, self.radius, fill=bg,
                               outline="")
            fg = self.fg_color if self._enabled else self._theme["DISABLED_FG"]
            self.create_text(w // 2, h // 2, text=self.text, fill=fg,
                             font=self.font, anchor="center")
        except tk.TclError:
            pass

    def _on_enter(self, _):
        if self._enabled:
            self._hovering = True
            self._draw()
            try:
                self.configure(cursor="hand2")
            except tk.TclError:
                pass

    def _on_leave(self, _):
        self._hovering = False
        self._draw()
        try:
            self.configure(cursor="")
        except tk.TclError:
            pass

    def _on_press(self, _):
        if self._enabled:
            try:
                self.move("all", 0, 1)
            except tk.TclError:
                pass

    def _on_release(self, event):
        if not self._enabled:
            return
        try:
            self.move("all", 0, -1)
        except tk.TclError:
            pass
        if (0 <= event.x <= int(self["width"])
                and 0 <= event.y <= int(self["height"])):
            if self.command:
                self.command()

    def set_enabled(self, enabled):
        self._enabled = enabled
        self._draw()

    def set_text(self, text, icon=""):
        self.text = f"{icon}  {text}" if icon else text
        self._draw()

    def set_colors(self, bg=None, hover=None, fg=None):
        if bg:
            self.bg_color = bg
        if hover:
            self.hover_color = hover
        if fg:
            self.fg_color = fg
        self._draw()


class Toast:
    _stack = []
    _lock = threading.Lock()

    @classmethod
    def show(cls, parent, message, kind="info", duration=2500, theme=None):
        theme = theme or THEMES["dark"]
        colors = {
            "info": (theme["ACCENT"], "ℹ"),
            "success": (theme["SUCCESS"], "✓"),
            "warning": (theme["WARNING"], "⚠"),
            "error": (theme["DANGER"], "✕"),
        }
        accent, icon = colors.get(kind, colors["info"])
        try:
            win = tk.Toplevel(parent)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-alpha", 0.96)
            except tk.TclError:
                pass
            frame = tk.Frame(win, bg=theme["TOAST_BG"], bd=0,
                             highlightthickness=1,
                             highlightbackground=accent)
            frame.pack(fill="both", expand=True)
            tk.Frame(frame, bg=accent, width=4).pack(side="left", fill="y")
            tk.Label(frame, text=icon, bg=theme["TOAST_BG"], fg=accent,
                     font=("Segoe UI", 14, "bold")).pack(side="left",
                                                          padx=(10, 6), pady=10)
            tk.Label(frame, text=message, bg=theme["TOAST_BG"],
                     fg=theme["FG_PRIMARY"],
                     font=("Segoe UI", 10)).pack(side="left",
                                                  padx=(0, 16), pady=10)
            win.update_idletasks()
            w, h = win.winfo_width(), win.winfo_height()
            px = parent.winfo_rootx() + parent.winfo_width() - w - 24
            py = parent.winfo_rooty() + parent.winfo_height() - h - 24
            with cls._lock:
                offset = sum(1 for t in cls._stack
                             if cls._safe_exists(t)) * (h + 8)
                cls._stack.append(win)
            win.geometry(f"+{px}+{py - offset}")

            def close():
                try:
                    win.destroy()
                except Exception:
                    pass
                with cls._lock:
                    if win in cls._stack:
                        cls._stack.remove(win)
            win.after(duration, close)
        except Exception as e:
            log.debug(f"Toast error: {e}")

    @staticmethod
    def _safe_exists(w):
        try:
            return w.winfo_exists()
        except Exception:
            return False


# ============================================================
#              КАНАЛ КАМЕРЫ
# ============================================================
class CameraChannel:
    def __init__(self, app, index):
        self.app = app
        self.index = index
        self.cap = None
        self.width = 1280
        self.height = 720
        self.actual_fps = 30.0
        self.last_frame = None
        self.frame_lock = threading.Lock()
        self._display_ready = False           # [OPT-2]
        self.capture_thread = None
        self.running = False
        self.online = False
        self.is_recording = False
        self.motion_recording = False
        self.video_writer = None
        self.current_video_path = None
        self.motion_timer_id = None
        self.recording_started_at = None
        self.recording_frames = 0
        self.total_recorded_sec = 0.0
        self.prev_gray = None
        self.bg_subtractor = None
        self.bg_frames = 0
        self.motion_confirm_counter = 0
        self.last_photo_time = 0.0
        self.last_video_time = 0.0
        self.motion_count = 0
        self._motion_active = False
        self.counter_manual = 0
        self.counter_video = 0
        self.counter_photo = 0
        self._photo_queue = queue.Queue(maxsize=512)
        self._photo_thread = None
        self._photo_thread_running = False
        self.audio_frames = []
        self.audio_stream = None
        self.audio_thread = None
        self.audio_recording = False
        self._audio_lock = threading.Lock()
        self.base_folder = ""
        self.photos_folder = ""
        self.videos_folder = ""
        self._photos_writable = False
        self._videos_writable = False
        self._refresh_folders()
        self._file_count_cache = (0, 0)
        self._file_count_cache_time = 0.0
        self._last_counters_update = 0.0     # [OPT-4]

        # [OPT-3] Motion worker
        self._motion_slot = None
        self._motion_slot_lock = threading.Lock()
        self._motion_wake = threading.Event()
        self._motion_running = True
        self._motion_thread = None

        self._start_photo_thread()
        self._start_motion_thread()

    def _refresh_folders(self):
        self.base_folder = os.path.join(self.app.output_path,
                                        f"Camera_{self.index}")
        self.photos_folder = os.path.join(self.base_folder, "Photos")
        self.videos_folder = os.path.join(self.base_folder, "Videos")
        self._photos_writable = False
        self._videos_writable = False
        for p, attr in ((self.base_folder, None),
                        (self.photos_folder, "_photos_writable"),
                        (self.videos_folder, "_videos_writable")):
            try:
                os.makedirs(p, exist_ok=True)
            except Exception as e:
                log.error(f"Не создать {p}: {e}")
                continue
            if attr:
                test = os.path.join(p, ".write_test")
                try:
                    with open(test, "wb") as f:
                        f.write(b"ok")
                    os.remove(test)
                    setattr(self, attr, True)
                except Exception as e:
                    log.error(f"CAM#{self.index}: НЕТ ПРАВ на {p}: {e}")
        if self._photos_writable and self._videos_writable:
            log.info(f"✅ CAM#{self.index}: папки готовы")

    def _ensure_folders(self):
        for p, attr in ((self.photos_folder, "_photos_writable"),
                        (self.videos_folder, "_videos_writable")):
            if not getattr(self, attr) or not os.path.isdir(p):
                try:
                    os.makedirs(p, exist_ok=True)
                    test = os.path.join(p, ".write_test")
                    with open(test, "wb") as f:
                        f.write(b"ok")
                    os.remove(test)
                    setattr(self, attr, True)
                except Exception:
                    setattr(self, attr, False)

    # [OPT-4] scandir вместо listdir + TTL 5 сек
    def count_files_on_disk(self, ttl=5.0):
        now = time.time()
        if now - self._file_count_cache_time < ttl:
            return self._file_count_cache
        n_photos = 0
        n_videos = 0
        try:
            with os.scandir(self.photos_folder) as it:
                for e in it:
                    if e.is_file() and e.name.lower().endswith(PHOTO_EXTS):
                        n_photos += 1
        except (FileNotFoundError, NotADirectoryError):
            pass
        except Exception:
            pass
        try:
            with os.scandir(self.videos_folder) as it:
                for e in it:
                    if e.is_file() and e.name.lower().endswith(VIDEO_EXTS):
                        n_videos += 1
        except (FileNotFoundError, NotADirectoryError):
            pass
        except Exception:
            pass
        self._file_count_cache = (n_photos, n_videos)
        self._file_count_cache_time = now
        return self._file_count_cache

    def invalidate_file_count_cache(self):
        self._file_count_cache_time = 0.0

    # ============ PHOTO THREAD ============
    def _start_photo_thread(self):
        if self._photo_thread is not None and self._photo_thread.is_alive():
            return
        self._photo_thread_running = True
        self._photo_thread = threading.Thread(
            target=self._photo_saver_loop, daemon=True,
            name=f"PhotoSaver-CAM{self.index}")
        self._photo_thread.start()

    # [OPT-6] Батч-drain
    def _photo_saver_loop(self):
        while self._photo_thread_running or not self._photo_queue.empty():
            batch = []
            try:
                while len(batch) < 8:
                    frame = self._photo_queue.get(timeout=0.2)
                    if frame is None:
                        self._photo_thread_running = False
                        try:
                            self._photo_queue.task_done()
                        except ValueError:
                            pass
                        break
                    batch.append(frame)
            except queue.Empty:
                pass
            for frame in batch:
                try:
                    if frame is not None:
                        self._save_photo_file(frame)
                finally:
                    try:
                        self._photo_queue.task_done()
                    except ValueError:
                        pass

    def _save_photo_file(self, frame):
        if frame is None or not hasattr(frame, "size") or frame.size == 0:
            return None
        self._ensure_folders()
        if not self._photos_writable:
            self.app.log(f"CAM#{self.index}: нет прав на запись фото",
                         "error")
            return None
        self.counter_photo += 1
        filename = make_recording_name("Фото", self.counter_photo, ".jpg",
                                       cam_index=self.index)
        filepath = os.path.join(self.photos_folder, filename)

        # [OVERLAY] Накладываем штамп CAM + дата + время на фото
        try:
            stamped = frame.copy()
            self._draw_overlay(stamped)
        except Exception as e:
            log.debug(f"overlay photo error: {e}")
            stamped = frame

        try:
            ok, buf = cv2.imencode(".jpg", stamped,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if not ok or buf is None:
                return None
            with open(filepath, "wb") as f:
                f.write(buf.tobytes())
        except Exception as e:
            self.app.log(f"❌ CAM#{self.index}: ошибка записи фото: {e}",
                         "error")
            return None
        try:
            sz = os.path.getsize(filepath)
        except Exception:
            sz = 0
        if sz <= 0:
            return None
        self.invalidate_file_count_cache()
        self.app.log(f"✅ CAM#{self.index}: фото ({sz} б) → {filepath}")
        self.app.ui_call(self.app.on_photo_saved, self.index, filepath)
        return filepath

    def _stop_photo_thread(self):
        self._photo_thread_running = False
        try:
            self._photo_queue.put_nowait(None)
        except Exception:
            pass
        try:
            while True:
                self._photo_queue.get_nowait()
                self._photo_queue.task_done()
        except queue.Empty:
            pass
        except ValueError:
            pass
        if self._photo_thread is not None:
            self._photo_thread.join(timeout=2.0)
        self._photo_thread = None

    def save_photo_now(self, frame):
        return self._save_photo_file(frame)

    def enqueue_photo(self, frame):
        try:
            self._photo_queue.put_nowait(frame.copy())
            return True
        except queue.Full:
            return False

    # ============ [OPT-3] MOTION WORKER ============
    def _start_motion_thread(self):
        if self._motion_thread is not None and self._motion_thread.is_alive():
            return
        self._motion_running = True
        self._motion_thread = threading.Thread(
            target=self._motion_worker, daemon=True,
            name=f"Motion-CAM{self.index}")
        self._motion_thread.start()

    def _motion_worker(self):
        while self._motion_running:
            self._motion_wake.wait(timeout=0.5)
            if not self._motion_running:
                break
            with self._motion_slot_lock:
                frame = self._motion_slot
                self._motion_slot = None
                self._motion_wake.clear()
            if frame is None:
                continue
            try:
                self._detect_motion(frame)
            except Exception as e:
                log.error(f"motion worker CAM#{self.index}: {e}")

    def _stop_motion_thread(self):
        self._motion_running = False
        self._motion_wake.set()
        if self._motion_thread is not None:
            self._motion_thread.join(timeout=1.5)
        self._motion_thread = None

    # ============ OPEN / CLOSE ============
    def open(self):
        if self.cap is not None and self.cap.isOpened():
            return True
        if IS_WINDOWS:
            preferred_apis = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
        elif IS_LINUX:
            preferred_apis = [cv2.CAP_V4L2, cv2.CAP_ANY]
        else:
            preferred_apis = [cv2.CAP_ANY]
        for api in preferred_apis:
            cap = cv2.VideoCapture(self.index, api)
            if not cap.isOpened():
                cap.release()
                continue
            frame = None
            for _ in range(3):
                ret, f = cap.read()
                if ret and f is not None:
                    frame = f
                    break
                time.sleep(0.03)
            if frame is None:
                cap.release()
                continue
            self.cap = cap
            self.width, self.height = self._get_max_resolution(cap)
            try:
                cap.set(cv2.CAP_PROP_FPS, self.app.target_fps)
            except Exception:
                pass
            self.actual_fps = self._measure_fps(cap, 10)
            if (not self.actual_fps or self.actual_fps < 1
                    or self.actual_fps > 240):
                self.actual_fps = 30.0
            self.running = True
            self.online = True
            self.capture_thread = threading.Thread(
                target=self._capture_loop, daemon=True,
                name=f"Capture-CAM{self.index}")
            self.capture_thread.start()
            self.app.log(f"CAM#{self.index}: захват запущен "
                         f"({self.width}x{self.height}@{self.actual_fps:.1f})")
            return True
        self.online = False
        return False

    def close(self):
        self.running = False
        if self.capture_thread is not None and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        if self.video_writer is not None:
            try:
                self.video_writer.release()
            except Exception:
                pass
            self.video_writer = None
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        self.online = False
        self.is_recording = False
        self.motion_recording = False
        self._stop_photo_thread()
        self._stop_motion_thread()

    def _get_max_resolution(self, cap):
        resolutions = [(3840, 2160), (2560, 1440), (1920, 1080),
                       (1280, 720), (1024, 768), (800, 600), (640, 480)]
        for w, h in resolutions:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if abs(aw - w) < 10 and abs(ah - h) < 10:
                return aw, ah
        return (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

    def _measure_fps(self, cap, num_frames=10):
        start = time.time()
        for _ in range(num_frames):
            ret, _ = cap.read()
            if not ret:
                break
        elapsed = time.time() - start
        return num_frames / elapsed if elapsed > 0 else 30.0

    def get_frame(self):
        with self.frame_lock:
            if not self._display_ready or self.last_frame is None:
                return None
            return self.last_frame

    def total_recorded(self):
        t = self.total_recorded_sec
        if ((self.is_recording or self.motion_recording)
                and self.recording_started_at):
            t += (datetime.now() - self.recording_started_at).total_seconds()
        return int(t)

    def is_active_recording(self):
        return self.is_recording or self.motion_recording

    # ============ CAPTURE LOOP ============
    def _capture_loop(self):
        while self.running and self.cap is not None and self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret or frame is None:
                time.sleep(0.005)
                continue

            if self.video_writer is not None:
                try:
                    self.video_writer.write(frame)
                    self.recording_frames += 1
                except Exception as e:
                    log.error(f"Ошибка записи (CAM#{self.index}): {e}")

            # [OPT-3] детекция в отдельном потоке, single-slot
            if self.app.photo_trap_enabled or self.app.video_trap_enabled:
                with self._motion_slot_lock:
                    self._motion_slot = frame.copy()
                self._motion_wake.set()

            # [OVERLAY] Наложение штампа CAM + дата + время
            self._draw_overlay(frame)

            # [OPT-2] Двойная буферизация: отдать кадр UI, старый — себе
            with self.frame_lock:
                self.last_frame, frame = frame, self.last_frame
                self._display_ready = True

    # ============ [OVERLAY] ШТАМП CAM + ДАТА + ВРЕМЯ ============
    def _draw_overlay(self, frame, target_width=None):
        """
        Рисует на кадре:
          • «CAM #N» — крупно, слева вверху (жёлтый, на плашке)
          • «● REC»  — справа вверху, если идёт запись (красный)
          • «03.10.2026  23:45:16» — снизу слева (белый, на плашке)
        Масштаб шрифта адаптируется от ширины кадра (640p → 4K).
        """
        h, w = frame.shape[:2]

        # Коэффициент масштаба относительно эталонной ширины 1280
        base_w = 1280.0
        k = (target_width or w) / base_w
        k = max(0.5, min(3.0, k))   # ограничим разумными пределами

        now = datetime.now()
        date_str = now.strftime("%d.%m.%Y")
        time_str = now.strftime("%H:%M:%S")

        # ---------- 1. CAM #N (сверху слева) ----------
        cam_text = f"CAM #{self.index}"
        cam_scale = 1.1 * k
        cam_thick = max(1, int(2 * k))
        (cw, ch), _ = cv2.getTextSize(cam_text, cv2.FONT_HERSHEY_SIMPLEX,
                                      cam_scale, cam_thick)

        pad = int(10 * k)
        # Полупрозрачная плашка под надписью
        overlay = frame.copy()
        cv2.rectangle(overlay,
                      (pad, pad),
                      (pad + cw + 2 * pad, pad + ch + 2 * pad),
                      (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

        cv2.putText(frame, cam_text,
                    (pad * 2, pad * 2 + ch),
                    cv2.FONT_HERSHEY_SIMPLEX, cam_scale,
                    (0, 255, 255), cam_thick, cv2.LINE_AA)

        # ---------- 2. REC (сверху справа) ----------
        if self.is_recording or self.motion_recording:
            rec_txt = "REC"
            rec_scale = 0.9 * k
            rec_thick = max(1, int(2 * k))
            (rw, rh), _ = cv2.getTextSize(rec_txt, cv2.FONT_HERSHEY_SIMPLEX,
                                          rec_scale, rec_thick)
            rx = w - rw - pad * 3 - int(20 * k)   # место под кружок
            ry = pad * 2 + rh

            # кружок
            dot_r = max(5, int(8 * k))
            cx = rx - dot_r - int(8 * k)
            cy = ry - rh // 2
            cv2.circle(frame, (cx, cy), dot_r, (0, 0, 255), -1)

            cv2.putText(frame, rec_txt, (rx, ry),
                        cv2.FONT_HERSHEY_SIMPLEX, rec_scale,
                        (0, 0, 255), rec_thick, cv2.LINE_AA)

        # ---------- 3. ДАТА + ВРЕМЯ (снизу слева) ----------
        dt_text = f"{date_str}  {time_str}"
        dt_scale = 1.0 * k
        dt_thick = max(1, int(2 * k))
        (dw, dh), _ = cv2.getTextSize(dt_text, cv2.FONT_HERSHEY_SIMPLEX,
                                      dt_scale, dt_thick)

        # Плашка снизу
        bx1 = pad
        by2 = h - pad
        by1 = by2 - dh - 2 * pad
        bx2 = pad + dw + 2 * pad
        overlay = frame.copy()
        cv2.rectangle(overlay, (bx1, by1), (bx2, by2), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

        cv2.putText(frame, dt_text,
                    (bx1 + pad, by2 - pad),
                    cv2.FONT_HERSHEY_SIMPLEX, dt_scale,
                    (255, 255, 255), dt_thick, cv2.LINE_AA)

        return frame

    # Оставляем для совместимости
    def _add_timestamp(self, frame):
        return self._draw_overlay(frame)

    # ============ [OPT-9] MOTION DETECT (downscale) ============
    def _detect_motion(self, frame):
        h_frame, w_frame = frame.shape[:2]

        # [OPT-9] downscale до MAX 640 по ширине
        scale = 1.0
        if w_frame > MOTION_DOWNSCALE_WIDTH:
            scale = MOTION_DOWNSCALE_WIDTH / w_frame
            new_w = MOTION_DOWNSCALE_WIDTH
            new_h = max(1, int(h_frame * scale))
            small = cv2.resize(frame, (new_w, new_h),
                               interpolation=cv2.INTER_AREA)
        else:
            small = frame
        h_s, w_s = small.shape[:2]

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self.bg_subtractor is None:
            self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
                history=200,
                varThreshold=max(4, self.app.motion_threshold // 4),
                detectShadows=True)
            self.bg_frames = 0

        if self.bg_frames > 0 and self.bg_frames % 1800 == 0:
            self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
                history=200,
                varThreshold=max(4, self.app.motion_threshold // 4),
                detectShadows=True)
            self.bg_frames = 0

        fg_mask = self.bg_subtractor.apply(gray, learningRate=0.01)
        self.bg_frames += 1
        if self.bg_frames < 5:
            fg_mask[:] = 0
        else:
            _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)

        kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel_open,
                                   iterations=1)
        kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel_close,
                                   iterations=2)

        diff_mask = None
        if self.prev_gray is not None:
            diff = cv2.absdiff(self.prev_gray, gray)
            _, diff_mask = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
            diff_mask = cv2.morphologyEx(diff_mask, cv2.MORPH_OPEN, kernel_open)
            diff_mask = cv2.morphologyEx(diff_mask, cv2.MORPH_CLOSE,
                                         kernel_close)
        self.prev_gray = gray

        if self.app.motion_ignore_zones:
            for (x1p, y1p, x2p, y2p) in self.app.motion_ignore_zones:
                x1 = int(w_s * x1p / 100)
                y1 = int(h_s * y1p / 100)
                x2 = int(w_s * x2p / 100)
                y2 = int(h_s * y2p / 100)
                cv2.rectangle(fg_mask, (x1, y1), (x2, y2), 0, -1)
                if diff_mask is not None:
                    cv2.rectangle(diff_mask, (x1, y1), (x2, y2), 0, -1)

        if diff_mask is not None:
            combined = cv2.bitwise_or(fg_mask, diff_mask)
        else:
            combined = fg_mask

        frame_area = w_s * h_s
        max_area = frame_area * self.app.motion_max_area_ratio
        # [OPT-9] масштабируем площади обратно
        area_k = scale * scale if scale > 0 else 1.0
        min_area = self.app.motion_min_area * area_k

        def _find_real_motion(mask):
            if mask is None:
                return False
            try:
                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                               cv2.CHAIN_APPROX_SIMPLE)
            except Exception:
                return False
            for c in contours:
                area = cv2.contourArea(c)
                if area < min_area:
                    continue
                if area > max_area:
                    continue
                hull = cv2.convexHull(c)
                hull_area = cv2.contourArea(hull)
                if hull_area <= 0:
                    continue
                if area / hull_area < self.app.motion_min_solidity:
                    continue
                x, y, w_, h_ = cv2.boundingRect(c)
                if w_ == 0 or h_ == 0:
                    continue
                if max(w_, h_) / min(w_, h_) > 6:
                    continue
                return True
            return False

        real_motion = _find_real_motion(combined)

        if real_motion:
            self.motion_confirm_counter += 1
        else:
            self.motion_confirm_counter = max(
                0, self.motion_confirm_counter - 1)

        confirmed = (self.motion_confirm_counter
                     >= self.app.motion_confirm_frames)
        was_active = self._motion_active
        self._motion_active = confirmed

        if confirmed and not was_active:
            self.app.log(f"CAM#{self.index}: ДВИЖЕНИЕ")

        if not confirmed:
            return

        now = time.time()
        if self.app.photo_trap_enabled:
            first_trigger = not was_active
            if first_trigger or (now - self.last_photo_time
                                 >= self.app.photo_cooldown):
                self.last_photo_time = now
                self.motion_count += 1
                self.enqueue_photo(frame)
                self.app.ui_call(self.app.notify_trap_triggered, "photo")

        if (self.app.video_trap_enabled
                and not self.is_recording
                and not self.motion_recording):
            if now - self.last_video_time >= self.app.video_cooldown:
                self.last_video_time = now
                self.app.ui_call(self.app.notify_trap_triggered, "video")
                self.start_motion_recording()

    # ============ AUDIO ============
    def start_audio(self):
        if not self.app.audio_enabled or not AUDIO_AVAILABLE:
            return
        with self._audio_lock:
            self.audio_recording = True
            self.audio_frames = []
            self.audio_thread = threading.Thread(
                target=self._record_audio, daemon=True,
                name=f"Audio-CAM{self.index}")
            self.audio_thread.start()

    def _record_audio(self):
        p = None
        try:
            p = pyaudio.PyAudio()
            self.audio_stream = p.open(
                format=self.app.audio_format,
                channels=self.app.audio_channels,
                rate=self.app.audio_sample_rate, input=True,
                frames_per_buffer=self.app.audio_chunk)
            while self.audio_recording:
                try:
                    data = self.audio_stream.read(
                        self.app.audio_chunk, exception_on_overflow=False)
                except Exception:
                    break
                self.audio_frames.append(data)
                try:
                    samples = np.frombuffer(data, dtype=np.int16)
                    peak = int(np.abs(samples).max()) if samples.size else 0
                    self.app.audio_level = min(
                        100, int(peak / 32768 * 100 * 2))
                    self.app.ui_call(self.app._draw_audio_meter)
                except Exception:
                    pass
            try:
                self.audio_stream.stop_stream()
                self.audio_stream.close()
            except Exception:
                pass
        except Exception as e:
            log.error(f"Ошибка аудио (CAM#{self.index}): {e}")
        finally:
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass

    def stop_audio(self, video_path):
        if not self.app.audio_enabled or not AUDIO_AVAILABLE:
            return video_path
        with self._audio_lock:
            self.audio_recording = False
            frames = self.audio_frames
            thr = self.audio_thread
        if thr is not None and thr.is_alive():
            thr.join(timeout=1.5)
        if not frames:
            return video_path
        if not _ffmpeg_exe:
            log.warning("ffmpeg недоступен — аудио не добавлено")
            self.app.audio_level = 0
            return video_path

        wav_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav",
                                             delete=False) as tmp:
                wav_path = tmp.name
            wf = wave.open(wav_path, "wb")
            wf.setnchannels(self.app.audio_channels)
            wf.setsampwidth(_get_sample_size(self.app.audio_format))
            wf.setframerate(self.app.audio_sample_rate)
            wf.writeframes(b"".join(frames))
            wf.close()
        except Exception as e:
            log.error(f"Ошибка записи WAV (CAM#{self.index}): {e}")
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass
            return video_path

        out = video_path.replace(self.app.extension,
                                 f"_audio{self.app.extension}")
        cmd = [_ffmpeg_exe, "-y", "-i", video_path, "-i", wav_path,
               "-c:v", "copy", "-c:a", "aac",
               "-map", "0:v:0", "-map", "1:a:0", "-shortest", out]
        try:
            kwargs = {"check": True, "capture_output": True, "timeout": 180}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.run(cmd, **kwargs)
            os.remove(video_path)
            os.replace(out, video_path)
        except FileNotFoundError:
            self.app.log("ffmpeg не найден — видео без звука", "warning")
        except Exception as e:
            log.error(f"Ошибка объединения (CAM#{self.index}): {e}")
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass
        self.app.audio_level = 0
        return video_path

    # ============ RECORDING ============
    def _make_writer(self, filepath):
        fourcc = cv2.VideoWriter_fourcc(*self.app.codec_map[self.app.codec][0])
        return cv2.VideoWriter(filepath, fourcc, self.actual_fps,
                               (self.width, self.height))

    def start_manual_recording(self):
        if self.is_recording:
            return False
        if self.motion_recording:
            self.stop_motion_recording()
        self._ensure_folders()
        if not self._videos_writable:
            self.app.log(f"CAM#{self.index}: нет прав на запись видео",
                         "error")
            return False
        self.counter_manual += 1
        filename = make_recording_name("Ручная", self.counter_manual,
                                       self.app.extension,
                                       cam_index=self.index)
        filepath = os.path.join(self.videos_folder, filename)
        self.video_writer = self._make_writer(filepath)
        if not self.video_writer.isOpened():
            self.counter_manual -= 1
            self.app.log(f"Не открыть VideoWriter (CAM#{self.index})",
                         "error")
            return False
        self.is_recording = True
        self.current_video_path = filepath
        self.recording_started_at = datetime.now()
        self.recording_frames = 0
        self.start_audio()
        self.invalidate_file_count_cache()
        self.app.log(f"CAM#{self.index}: ручная запись → {filepath}")
        return True

    def stop_manual_recording(self):
        if not self.is_recording:
            return
        self.is_recording = False
        self._finish_recording()

    def start_motion_recording(self):
        if self.motion_recording or self.is_recording:
            return
        self._ensure_folders()
        if not self._videos_writable:
            return
        self.counter_video += 1
        filename = make_recording_name("Видео", self.counter_video,
                                       self.app.extension,
                                       cam_index=self.index)
        filepath = os.path.join(self.videos_folder, filename)
        self.video_writer = self._make_writer(filepath)
        if not self.video_writer.isOpened():
            self.counter_video -= 1
            return
        self.motion_recording = True
        self.current_video_path = filepath
        self.recording_started_at = datetime.now()
        self.recording_frames = 0
        self.start_audio()
        self.motion_timer_id = self.app.root.after(
            self.app.auto_stop_ms, self.stop_motion_recording)
        self.invalidate_file_count_cache()
        self.app.log(f"CAM#{self.index}: авто-запись → {filepath}")
        self.app.ui_call(self.app.on_motion_recording_started, self.index)

    def stop_motion_recording(self):
        if not self.motion_recording:
            return
        if self.motion_timer_id is not None:
            try:
                self.app.root.after_cancel(self.motion_timer_id)
            except Exception:
                pass
            self.motion_timer_id = None
        self.motion_recording = False
        self._finish_recording()
        self.app.ui_call(self.app.on_motion_recording_stopped, self.index)

    def _finish_recording(self):
        if self.video_writer is not None:
            try:
                self.video_writer.release()
            except Exception:
                pass
            self.video_writer = None
        if self.recording_started_at:
            self.total_recorded_sec += (
                datetime.now() - self.recording_started_at).total_seconds()
        self.recording_started_at = None
        video_path = self.current_video_path
        self.invalidate_file_count_cache()
        self.app.ui_call(self.app._update_counters_ui)
        threading.Thread(target=self._finalize_recording_async,
                         args=(video_path,), daemon=True,
                         name=f"Finalize-CAM{self.index}").start()

    def _finalize_recording_async(self, video_path):
        try:
            self.stop_audio(video_path)
        except Exception as e:
            log.error(f"Ошибка финализации (CAM#{self.index}): {e}")
        self.invalidate_file_count_cache()
        self.app.ui_call(self.app.on_recording_finished, self.index)


# ============================================================
#                ОСНОВНОЕ ПРИЛОЖЕНИЕ
# ============================================================
class VideoRecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)

        try:
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
        except Exception:
            sw, sh = 1366, 768
        win_w = max(900, min(1320, int(sw * 0.92)))
        win_h = max(600, min(880, int(sh * 0.90)))
        self.root.geometry(f"{win_w}x{win_h}")
        self.root.minsize(900, 600)
        try:
            x = max(0, (sw - win_w) // 2)
            y = max(0, (sh - win_h) // 2)
            self.root.geometry(f"{win_w}x{win_h}+{x}+{y}")
        except Exception:
            pass

        self._screen_w = sw
        self._screen_h = sh
        self._narrow_mode = win_w < 1200
        self._compact_mode = win_h < 720
        self._last_root_size = (win_w, win_h)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.theme_name = "dark"
        self.theme = THEMES[self.theme_name]
        self.root.configure(bg=self.theme["BG_MAIN"])

        self._ui_queue = queue.Queue()
        self._ui_queue_running = True

        self.target_fps = 30
        self.codec = "XVID"
        self.extension = ".avi"
        self.codec_map = {
            "XVID": ("XVID", ".avi"),
            "MP4V": ("mp4v", ".mp4"),
            "MJPG": ("MJPG", ".avi"),
        }

        self.channels = {}
        self.display_index = None
        self.available_cameras = []
        self.grid_view = True
        self._last_grid_update = 0.0
        self._last_single_update = 0.0

        # === АРХИВ ===
        self.archive_view = False
        self.archive_tab = "video"
        self.archive_files = []
        self.archive_photo_files = []
        self.archive_photo_cells = {}
        self.archive_selected_photo = None
        self.archive_preview_window = None
        self.archive_preview_playing = False
        self.archive_playing = False
        self.archive_current_path = None
        self.archive_fps = 30.0
        self.archive_duration = 0.0
        self.archive_position = 0.0
        self.archive_frame_count = 0
        self.archive_frame_index = 0
        self.archive_last_ts = 0.0
        self.archive_current_frame = None
        self.archive_frame_lock = threading.Lock()
        self.archive_worker_thread = None
        self.archive_worker_stop = threading.Event()
        self.archive_pause_event = threading.Event()
        self.archive_pause_event.set()
        self.archive_paused = False
        self.archive_speed = 1.0
        self.archive_seek_request = None
        self.archive_seek_lock = threading.Lock()
        self.archive_saved_frame_path = None
        self.archive_step_frame = 0
        self.archive_step_lock = threading.Lock()
        self.archive_slider_dragging = False
        self.archive_slider_pos = 0.0

        self._cam_scan_cache = []
        self._cam_scan_cache_time = 0.0
        self.photo_trap_enabled = False
        self.video_trap_enabled = False
        self.motion_threshold = 100
        self.photo_cooldown = 0.0
        self.video_cooldown = 1.5
        self.motion_min_area = 1500
        self.motion_max_area_ratio = 0.6
        self.motion_min_solidity = 0.35
        self.motion_confirm_frames = 1
        self.motion_ignore_zones = []
        self.sensitivity_level = "medium"

        self.output_path = os.path.join(os.path.expanduser("~"),
                                        "Videos", "Fotolovushka")
        try:
            os.makedirs(self.output_path, exist_ok=True)
        except Exception as e:
            log.error(f"Не создать {self.output_path}: {e}")
            self.output_path = os.path.join(os.path.expanduser("~"),
                                            "Fotolovushka")
            os.makedirs(self.output_path, exist_ok=True)
        self.photos_folder = os.path.join(self.output_path, "Photos")
        os.makedirs(self.photos_folder, exist_ok=True)

        self.audio_enabled = AUDIO_AVAILABLE
        self.audio_sample_rate = 44100
        self.audio_channels = 2
        self.audio_format = pyaudio.paInt16 if AUDIO_AVAILABLE else 0
        self.audio_chunk = 1024
        self.audio_level = 0

        self.start_fullscreen = True
        self.autostart_enabled = False
        self.auto_enable_traps = True
        self.trap_alert_sound_enabled = True
        self.trap_sound_enabled = True
        self.trap_sound_mode = "both"
        self.trap_sound_min_interval = 1.5
        self._last_trap_sound_time = 0.0
        self._trap_sound_lock = threading.Lock()
        self._sound_playing = False
        self._blink_state = False
        self.session_start = datetime.now()
        self.log_queue = queue.Queue()
        self._fullscreen_active = False
        self._active_manual_recordings = False
        self._screen_is_off = False
        self._sleep_blocker_active = False

        self._config_save_timer = None
        self._config_save_lock = threading.Lock()

        self._thumb_pool = ThreadPoolExecutor(
            max_workers=THUMB_WORKERS, thread_name_prefix="Thumb")
        self._scan_pool = ThreadPoolExecutor(
            max_workers=SCAN_WORKERS, thread_name_prefix="Scan")
        self._open_pool = ThreadPoolExecutor(
            max_workers=OPEN_WORKERS, thread_name_prefix="Open")
        self._control_pool = ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="Control")

        # [OPT-5] Ограничение очереди миниатюр
        self._thumb_futures = {}
        self._thumb_semaphore = threading.Semaphore(THUMB_WORKERS * 4)

        self._last_counters_update = 0.0    # [OPT-4]

        self._photo_wav_ready = generate_photo_signal_wav()
        self._video_wav_ready = generate_video_signal_wav()

        self.load_config()
        self._sync_autostart_state()
        self._setup_styles()
        self.create_widgets()
        self.bind_hotkeys()

        self._poll_ui_queue()

        if self.start_fullscreen:
            self.root.after(150, self._enter_fullscreen)
        self.root.after(200, self._start_sleep_blocker)
        self.root.after(200, self.initial_connect_all)
        # [OPT-10] единый heartbeat
        self.root.after(500, self._ui_heartbeat)
        self._process_log_queue()
        self._update_display_loop()
        self.root.after(1200, self._auto_enable_traps)
        self.root.after(1500, self._report_archive_backend)

        self.root.bind("<Configure>", self._on_root_resize)

    # ============ UI-QUEUE ============
    def ui_call(self, fn, *args, **kwargs):
        if not self._ui_queue_running:
            return
        try:
            self._ui_queue.put_nowait((fn, args, kwargs))
        except queue.Full:
            pass

    def _poll_ui_queue(self):
        if not self._ui_queue_running:
            return
        try:
            count = 0
            while count < 100:
                fn, args, kwargs = self._ui_queue.get_nowait()
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    log.error(f"UI-call error: {e}")
                count += 1
        except queue.Empty:
            pass
        try:
            self.root.after(16, self._poll_ui_queue)
        except tk.TclError:
            pass

    def _report_archive_backend(self):
        if ARCHIVE_PLAYER_AVAILABLE:
            self.log(f"[ARCHIVE] Плеер готов: {ARCHIVE_PLAYER_BACKEND}")
        else:
            self.log("[ARCHIVE] Плеер недоступен: установите imageio-ffmpeg "
                     "или системный ffmpeg", "warning")
        if not AUDIO_AVAILABLE:
            self.log("[AUDIO] pyaudio не установлен — запись звука "
                     "отключена", "warning")
        if not _ffmpeg_exe:
            self.log("[FFMPEG] ffmpeg не найден — аудио не будет "
                     "объединяться с видео", "warning")

    def _on_root_resize(self, event):
        if event.widget is not self.root:
            return
        try:
            w, h = event.width, event.height
        except Exception:
            return
        if (w, h) == getattr(self, "_last_root_size", None):
            return
        self._last_root_size = (w, h)
        new_narrow = w < 1200
        new_compact = h < 720
        if (new_narrow != self._narrow_mode
                or new_compact != self._compact_mode):
            self._narrow_mode = new_narrow
            self._compact_mode = new_compact
            try:
                if hasattr(self, "right_panel"):
                    self.right_panel.config(
                        width=340 if not new_narrow else 300)
            except Exception:
                pass
            if self.channels and self.grid_view:
                self.root.after(100, self._rebuild_grid)

    def _sync_autostart_state(self):
        try:
            self.autostart_enabled = is_autostart_enabled()
        except Exception:
            self.autostart_enabled = False

    def _start_sleep_blocker(self):
        if not IS_WINDOWS:
            return

        def worker():
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_AWAYMODE_REQUIRED = 0x00000040
            flags = (ES_CONTINUOUS | ES_SYSTEM_REQUIRED
                     | ES_AWAYMODE_REQUIRED)
            while True:
                try:
                    ctypes.windll.kernel32.SetThreadExecutionState(flags)
                except Exception:
                    pass
                time.sleep(30)
        threading.Thread(target=worker, daemon=True,
                         name="SleepBlocker").start()
        self._sleep_blocker_active = True

    def _screen_off_available(self):
        return IS_WINDOWS

    def turn_screen_off(self):
        if not self._screen_off_available():
            Toast.show(self.root, "Только Windows",
                       kind="warning", theme=self.theme)
            return
        try:
            self.root.focus_force()
            self.root.update()
            time.sleep(0.1)
            HWND_BROADCAST = 0xFFFF
            WM_SYSCOMMAND = 0x0112
            SC_MONITORPOWER = 0xF170
            MONITOR_OFF = 2
            ctypes.windll.user32.SendMessageW(
                HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF)
            self._screen_is_off = True
        except Exception as e:
            Toast.show(self.root, f"Ошибка: {e}",
                       kind="error", theme=self.theme)

    def _wake_screen(self, _event=None):
        if not getattr(self, "_screen_is_off", False):
            return
        self._screen_is_off = False
        if not self._screen_off_available():
            return
        try:
            HWND_BROADCAST = 0xFFFF
            WM_SYSCOMMAND = 0x0112
            SC_MONITORPOWER = 0xF170
            MONITOR_ON = -1
            ctypes.windll.user32.SendMessageW(
                HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_ON)
        except Exception:
            pass

    def _play_wav_async(self, wav_path):
        def worker():
            played = False
            if IS_WINDOWS and winsound and wav_path and os.path.exists(wav_path):
                try:
                    winsound.PlaySound(
                        wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC
                        | winsound.SND_NODEFAULT)
                    played = True
                except Exception:
                    played = False
            if not played and wav_path and os.path.exists(wav_path):
                try:
                    self._play_wav_via_pyaudio(wav_path)
                    played = True
                except Exception:
                    played = False
            if not played:
                try:
                    self.root.bell()
                except Exception:
                    pass
        threading.Thread(target=worker, daemon=True,
                         name="WavPlayer").start()

    def _play_wav_via_pyaudio(self, wav_path):
        if not AUDIO_AVAILABLE:
            raise RuntimeError("pyaudio недоступен")
        wf = wave.open(wav_path, "rb")
        p = pyaudio.PyAudio()
        try:
            stream = p.open(
                format=p.get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=wf.getframerate(), output=True)
            chunk = 1024
            data = wf.readframes(chunk)
            while data:
                stream.write(data)
                data = wf.readframes(chunk)
            stream.stop_stream()
            stream.close()
        finally:
            try:
                p.terminate()
            except Exception:
                pass

    def play_sound_signal(self):
        if self._sound_playing:
            return
        self._sound_playing = True
        self._play_wav_async(SIGNAL_PHOTO_WAV_PATH)
        self.root.after(600, lambda: setattr(self, "_sound_playing", False))
        Toast.show(self.root, "🔔  Звуковой сигнал",
                   kind="info", theme=self.theme, duration=1200)

    def notify_trap_triggered(self, kind):
        if not self.trap_alert_sound_enabled or not self.trap_sound_enabled:
            return
        if self.trap_sound_mode == "photo" and kind != "photo":
            return
        if self.trap_sound_mode == "video" and kind != "video":
            return
        if self._sound_playing:
            return
        now = time.time()
        with self._trap_sound_lock:
            if now - self._last_trap_sound_time < self.trap_sound_min_interval:
                return
            self._last_trap_sound_time = now
        self._sound_playing = True
        self.root.after(900, lambda: setattr(self, "_sound_playing", False))
        wav = (SIGNAL_PHOTO_WAV_PATH if kind == "photo"
               else SIGNAL_VIDEO_WAV_PATH)
        self._play_wav_async(wav)

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            if not hasattr(self, "motion_duration_sec"):
                self.motion_duration_sec = 15 * 60
            if not hasattr(self, "auto_stop_ms"):
                self.auto_stop_ms = self.motion_duration_sec * 1000
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved_path = data.get("output_path", "")
            if saved_path and "Фотоловушка" not in saved_path:
                self.output_path = saved_path
            os.makedirs(self.output_path, exist_ok=True)
            self.photos_folder = os.path.join(self.output_path, "Photos")
            os.makedirs(self.photos_folder, exist_ok=True)
            self.target_fps = data.get("target_fps", self.target_fps)
            self.motion_threshold = data.get("motion_threshold",
                                             self.motion_threshold)
            self.photo_cooldown = data.get("photo_cooldown",
                                           self.photo_cooldown)
            self.video_cooldown = data.get("video_cooldown",
                                           self.video_cooldown)
            self.audio_enabled = data.get(
                "audio_enabled", self.audio_enabled) and AUDIO_AVAILABLE
            self.codec = data.get("codec", self.codec)
            if self.codec in self.codec_map:
                self.extension = self.codec_map[self.codec][1]
            self.theme_name = data.get("theme", self.theme_name)
            self.theme = THEMES.get(self.theme_name, THEMES["dark"])
            self.motion_duration_sec = data.get("motion_duration_sec",
                                                15 * 60)
            self.auto_stop_ms = self.motion_duration_sec * 1000
            self.start_fullscreen = data.get("start_fullscreen", True)
            self.sensitivity_level = data.get("sensitivity_level", "medium")
            self.motion_min_area = data.get("motion_min_area", 1500)
            self.motion_max_area_ratio = data.get(
                "motion_max_area_ratio", 0.6)
            self.motion_confirm_frames = data.get("motion_confirm_frames", 1)
            self.motion_min_solidity = data.get("motion_min_solidity", 0.35)
            self.motion_ignore_zones = [
                tuple(z) for z in data.get("motion_ignore_zones", [])]
            self.auto_enable_traps = data.get("auto_enable_traps", True)
            self.trap_alert_sound_enabled = data.get(
                "trap_alert_sound_enabled", True)
            self.trap_sound_enabled = data.get("trap_sound_enabled",
                                               self.trap_sound_enabled)
            self.trap_sound_mode = data.get("trap_sound_mode",
                                            self.trap_sound_mode)
            self.trap_sound_min_interval = data.get(
                "trap_sound_min_interval", self.trap_sound_min_interval)
            self.grid_view = data.get("grid_view", True)
        except Exception as e:
            log.error(f"Ошибка конфига: {e}")
        if not hasattr(self, "motion_duration_sec"):
            self.motion_duration_sec = 15 * 60
        if not hasattr(self, "auto_stop_ms"):
            self.auto_stop_ms = self.motion_duration_sec * 1000

    def save_config(self):
        with self._config_save_lock:
            if self._config_save_timer is not None:
                try:
                    self.root.after_cancel(self._config_save_timer)
                except Exception:
                    pass
            self._config_save_timer = self.root.after(
                500, self._save_config_now)

    def _save_config_now(self):
        self._config_save_timer = None
        channels_data = {}
        for idx, ch in self.channels.items():
            channels_data[str(idx)] = {
                "counter_manual": ch.counter_manual,
                "counter_video": ch.counter_video,
                "counter_photo": ch.counter_photo,
            }
        data = {
            "output_path": self.output_path,
            "target_fps": self.target_fps,
            "motion_threshold": self.motion_threshold,
            "photo_cooldown": self.photo_cooldown,
            "video_cooldown": self.video_cooldown,
            "audio_enabled": self.audio_enabled,
            "codec": self.codec,
            "theme": self.theme_name,
            "motion_duration_sec": getattr(self, "motion_duration_sec", 900),
            "start_fullscreen": self.start_fullscreen,
            "sensitivity_level": self.sensitivity_level,
            "motion_min_area": self.motion_min_area,
            "motion_max_area_ratio": self.motion_max_area_ratio,
            "motion_confirm_frames": self.motion_confirm_frames,
            "motion_min_solidity": self.motion_min_solidity,
            "motion_ignore_zones": [list(z) for z in self.motion_ignore_zones],
            "auto_enable_traps": self.auto_enable_traps,
            "trap_alert_sound_enabled": self.trap_alert_sound_enabled,
            "trap_sound_enabled": self.trap_sound_enabled,
            "trap_sound_mode": self.trap_sound_mode,
            "trap_sound_min_interval": self.trap_sound_min_interval,
            "channels": channels_data,
            "grid_view": self.grid_view,
        }
        try:
            tmp = CONFIG_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, CONFIG_FILE)
        except Exception as e:
            log.error(f"Ошибка сохранения конфига: {e}")

    def _restore_channel_counters(self):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            channels_data = data.get("channels", {})
            for idx_str, counters in channels_data.items():
                try:
                    idx = int(idx_str)
                except ValueError:
                    continue
                ch = self.channels.get(idx)
                if ch is None:
                    continue
                ch.counter_manual = counters.get("counter_manual", 0)
                ch.counter_video = counters.get("counter_video", 0)
                ch.counter_photo = counters.get("counter_photo", 0)
        except Exception:
            pass

    def _setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        t = self.theme
        style.configure("Dark.TCombobox",
                        fieldbackground=t["BG_INPUT"],
                        background=t["BG_INPUT"],
                        foreground=t["FG_PRIMARY"],
                        arrowcolor=t["FG_PRIMARY"],
                        bordercolor=t["BORDER"],
                        lightcolor=t["BORDER"], darkcolor=t["BORDER"],
                        selectbackground=t["ACCENT"],
                        selectforeground="#ffffff")
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", t["BG_INPUT"])],
                  foreground=[("readonly", t["FG_PRIMARY"])])
        style.configure("Dark.TCheckbutton",
                        background=t["BG_CARD"],
                        foreground=t["FG_PRIMARY"],
                        focuscolor=t["BG_CARD"])
        style.map("Dark.TCheckbutton",
                  background=[("active", t["BG_CARD"])],
                  foreground=[("active", t["FG_PRIMARY"])])
        style.configure("Dark.TRadiobutton",
                        background=t["BG_CARD"],
                        foreground=t["FG_PRIMARY"],
                        focuscolor=t["BG_CARD"], font=("Segoe UI", 9))
        style.map("Dark.TRadiobutton",
                  background=[("active", t["BG_CARD"])],
                  foreground=[("active", t["FG_PRIMARY"])])
        style.configure("Dark.TNotebook",
                        background=t["BG_MAIN"], borderwidth=0)
        style.configure("Dark.TNotebook.Tab",
                        background=t["BG_PANEL"],
                        foreground=t["FG_SECONDARY"],
                        padding=[16, 7], font=("Segoe UI", 9), borderwidth=0)
        style.map("Dark.TNotebook.Tab",
                  background=[("selected", t["BG_CARD"])],
                  foreground=[("selected", t["ACCENT"])])
        style.configure("Accent.Horizontal.TProgressbar",
                        background=t["ACCENT"], troughcolor=t["BG_INPUT"],
                        bordercolor=t["BG_INPUT"], lightcolor=t["ACCENT"],
                        darkcolor=t["ACCENT"], thickness=6)

    def _enter_fullscreen(self):
        try:
            self.root.attributes("-fullscreen", True)
            self._fullscreen_active = True
            try:
                self.fs_btn.set_text("Выйти", "⛶")
            except Exception:
                pass
        except Exception as e:
            log.error(f"Ошибка полного экрана: {e}")

    def _exit_fullscreen(self):
        try:
            self.root.attributes("-fullscreen", False)
            self._fullscreen_active = False
            try:
                self.fs_btn.set_text("Экран", "⛶")
            except Exception:
                pass
        except Exception:
            pass

    def toggle_fullscreen(self):
        if self._fullscreen_active:
            self._exit_fullscreen()
        else:
            self._enter_fullscreen()

    # ============ UI ============
    def create_widgets(self):
        t = self.theme
        header = tk.Frame(self.root, bg=t["BG_PANEL"], height=54)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="⬤", bg=t["BG_PANEL"], fg=t["ACCENT"],
                 font=("Segoe UI", 15)).pack(side="left",
                                             padx=(16, 6), pady=12)
        tk.Label(header, text=APP_NAME, bg=t["BG_PANEL"],
                 fg=t["FG_PRIMARY"],
                 font=("Segoe UI", 13, "bold")).pack(side="left", pady=12)

        self.theme_btn = RoundedButton(
            header, "☀" if self.theme_name == "dark" else "🌙",
            command=self.toggle_theme,
            width=44, height=30, radius=8,
            bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
            hover_bg=t["BG_HOVER"], theme=t,
            font=("Segoe UI", 12))
        self.theme_btn.pack(side="right", padx=(0, 12), pady=12)

        self.fs_btn = RoundedButton(
            header, "Экран",
            command=self.toggle_fullscreen,
            width=88, height=30, radius=8,
            bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
            hover_bg=t["ACCENT"], theme=t,
            font=("Segoe UI", 9), icon="⛶")
        self.fs_btn.pack(side="right", padx=(0, 6), pady=12)

        self.view_btn = RoundedButton(
            header, "Сетка" if self.grid_view else "Одна",
            command=self.toggle_grid_view,
            width=80, height=30, radius=8,
            bg=t["ACCENT"] if self.grid_view else t["BG_INPUT"],
            fg="#ffffff" if self.grid_view else t["FG_PRIMARY"],
            hover_bg=t["ACCENT_HOVER"], theme=t,
            font=("Segoe UI", 9, "bold"))
        self.view_btn.pack(side="right", padx=(0, 6), pady=12)

        self.archive_btn = RoundedButton(
            header, "Архив",
            command=self.toggle_archive_view,
            width=88, height=30, radius=8,
            bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
            hover_bg=t["ACCENT"], theme=t,
            font=("Segoe UI", 9, "bold"))
        self.archive_btn.pack(side="right", padx=(0, 6), pady=12)

        self.header_status = tk.Label(
            header, text="●  Ожидание", bg=t["BG_PANEL"],
            fg=t["FG_SECONDARY"], font=("Segoe UI", 9))
        self.header_status.pack(side="right", padx=(0, 10))

        main = tk.Frame(self.root, bg=t["BG_MAIN"])
        main.pack(fill="both", expand=True, padx=10, pady=10)
        self.main_container = main

        self.cameras_panel = tk.Frame(main, bg=t["BG_MAIN"])
        self.cameras_panel.pack(side="left", fill="both", expand=True)

        right_width = 340 if not self._narrow_mode else 300
        self.right_panel = tk.Frame(main, bg=t["BG_MAIN"], width=right_width)
        self.right_panel.pack(side="right", fill="y", padx=(10, 0))
        self.right_panel.pack_propagate(False)

        self._right_canvas = tk.Canvas(
            self.right_panel, bg=t["BG_MAIN"], highlightthickness=0, bd=0)
        self._right_scroll = tk.Scrollbar(
            self.right_panel, orient="vertical",
            command=self._right_canvas.yview)
        self._right_canvas.configure(yscrollcommand=self._right_scroll.set)
        self._right_canvas.pack(side="left", fill="both", expand=True)
        self._right_scroll.pack(side="right", fill="y")

        self.control_panel = tk.Frame(self._right_canvas, bg=t["BG_MAIN"])
        self._right_canvas_window = self._right_canvas.create_window(
            (0, 0), window=self.control_panel, anchor="nw")

        def _right_inner_config(_e=None):
            try:
                self._right_canvas.configure(
                    scrollregion=self._right_canvas.bbox("all"))
            except Exception:
                pass

        def _right_canvas_config(e):
            try:
                self._right_canvas.itemconfig(
                    self._right_canvas_window, width=e.width)
            except Exception:
                pass

        self.control_panel.bind("<Configure>", _right_inner_config)
        self._right_canvas.bind("<Configure>", _right_canvas_config)

        def _on_right_wheel(event):
            try:
                delta = -1 if event.delta > 0 else 1
                self._right_canvas.yview_scroll(delta * 2, "units")
            except Exception:
                pass

        def _bind_wheel(_e=None):
            try:
                self.root.bind_all("<MouseWheel>", _on_right_wheel)
            except Exception:
                pass

        def _unbind_wheel(_e=None):
            try:
                self.root.unbind_all("<MouseWheel>")
            except Exception:
                pass

        self._right_canvas.bind("<Enter>", _bind_wheel)
        self._right_canvas.bind("<Leave>", _unbind_wheel)
        self.control_panel.bind("<Enter>", _bind_wheel)

        self._build_video_panel(self.cameras_panel)
        self._build_control_panel(self.control_panel)
        self._build_archive_panel(main)

    def _build_video_panel(self, parent):
        t = self.theme
        border = tk.Frame(parent, bg=t["BORDER"])
        border.pack(fill="both", expand=True)
        self.video_container = tk.Frame(border, bg="#000000")
        self.video_container.pack(fill="both", expand=True, padx=2, pady=2)
        self.video_label = tk.Label(self.video_container, bg="#000000", bd=0,
                                    text="📷  Инициализация камер...",
                                    fg=t["FG_MUTED"], font=("Segoe UI", 12))
        self.video_label.pack(fill="both", expand=True)
        self.camera_labels = {}
        self._grid_frame = None

        toolbar = tk.Frame(parent, bg=t["BG_MAIN"], height=76)
        toolbar.pack(fill="x", pady=(8, 0))
        toolbar.pack_propagate(False)
        info = tk.Frame(toolbar, bg=t["BG_CARD"])
        info.pack(side="left", fill="both", expand=True)
        self.file_info_label = tk.Label(info, text="📁  Файл: не выбран",
                                        bg=t["BG_CARD"],
                                        fg=t["FG_SECONDARY"],
                                        font=("Segoe UI", 9), anchor="w")
        self.file_info_label.pack(fill="x", padx=10, pady=(8, 2))
        self.rec_type_label = tk.Label(info, text="Тип записи: нет",
                                       bg=t["BG_CARD"], fg=t["ACCENT"],
                                       font=("Segoe UI", 9), anchor="w")
        self.rec_type_label.pack(fill="x", padx=10)
        self.timer_label = tk.Label(info,
                                    text="⏱  00:00:00   •   FPS: --",
                                    bg=t["BG_CARD"], fg=t["FG_MUTED"],
                                    font=("Consolas", 9), anchor="w")
        self.timer_label.pack(fill="x", padx=10, pady=(2, 8))

        counter_box = tk.Frame(toolbar, bg=t["BG_CARD"], width=170)
        counter_box.pack(side="right", fill="y", padx=(8, 0))
        counter_box.pack_propagate(False)
        tk.Label(counter_box, text="ФАЙЛЫ НА ДИСКЕ",
                 bg=t["BG_CARD"], fg=t["FG_MUTED"],
                 font=("Segoe UI", 7, "bold")).pack(pady=(6, 2))
        counters_row = tk.Frame(counter_box, bg=t["BG_CARD"])
        counters_row.pack(fill="both", expand=True, padx=5, pady=(2, 6))
        photo_sub = tk.Frame(counters_row, bg=t["BG_INPUT"])
        photo_sub.pack(side="left", fill="both", expand=True, padx=(0, 3))
        tk.Label(photo_sub, text="📷", bg=t["BG_INPUT"], fg=t["ACCENT"],
                 font=("Segoe UI", 10)).pack(pady=(5, 0))
        self.photo_count_label = tk.Label(
            photo_sub, text="0", bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
            font=("Consolas", 14, "bold"))
        self.photo_count_label.pack()
        tk.Label(photo_sub, text="ФОТО", bg=t["BG_INPUT"], fg=t["FG_MUTED"],
                 font=("Segoe UI", 7, "bold")).pack(pady=(0, 5))
        video_sub = tk.Frame(counters_row, bg=t["BG_INPUT"])
        video_sub.pack(side="left", fill="both", expand=True, padx=(3, 0))
        tk.Label(video_sub, text="🎥", bg=t["BG_INPUT"], fg=t["WARNING"],
                 font=("Segoe UI", 10)).pack(pady=(5, 0))
        self.video_count_label = tk.Label(
            video_sub, text="0", bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
            font=("Consolas", 14, "bold"))
        self.video_count_label.pack()
        tk.Label(video_sub, text="ВИДЕО", bg=t["BG_INPUT"], fg=t["FG_MUTED"],
                 font=("Segoe UI", 7, "bold")).pack(pady=(0, 5))

    def _build_control_panel(self, parent):
        t = self.theme
        self._btn_w = 300 if not self._narrow_mode else 260
        self._btn_h = 40 if not self._compact_mode else 36
        self._btn_h_sm = 36 if not self._compact_mode else 32

        self._section(parent, "ОСНОВНЫЕ ДЕЙСТВИЯ")
        actions = tk.Frame(parent, bg=t["BG_CARD"])
        actions.pack(fill="x", pady=(4, 10))
        self.btn_start = RoundedButton(
            actions, "Начать запись (все)",
            command=self.start_recording_all,
            width=self._btn_w, height=self._btn_h, radius=10,
            bg=t["SUCCESS_DARK"], fg="#ffffff",
            hover_bg=t["SUCCESS"], theme=t,
            font=("Segoe UI", 10, "bold"), icon="⏺")
        self.btn_start.pack(padx=10, pady=(10, 5))
        self.btn_stop = RoundedButton(
            actions, "Остановить",
            command=self.stop_recording_all,
            width=self._btn_w, height=self._btn_h_sm, radius=10,
            bg=t["DISABLED_BG"], fg=t["DISABLED_FG"],
            hover_bg=t["DANGER_HOVER"], theme=t,
            font=("Segoe UI", 10, "bold"), icon="⏹")
        self.btn_stop.pack(padx=10, pady=(0, 5))
        self.btn_stop.set_enabled(False)
        self.btn_settings = RoundedButton(
            actions, "Настройки", command=self.open_settings,
            width=self._btn_w, height=self._btn_h_sm, radius=10,
            bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
            hover_bg=t["ACCENT"], theme=t,
            font=("Segoe UI", 10), icon="⚙")
        self.btn_settings.pack(padx=10, pady=(0, 10))

        self._section(parent, "ОБСЛУЖИВАНИЕ")
        service = tk.Frame(parent, bg=t["BG_CARD"])
        service.pack(fill="x", pady=(4, 10))
        self.btn_screen = RoundedButton(
            service, "Выключить экран", command=self.turn_screen_off,
            width=self._btn_w, height=self._btn_h_sm, radius=10,
            bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
            hover_bg=t["WARNING"], theme=t,
            font=("Segoe UI", 9), icon="🖥")
        self.btn_screen.pack(padx=10, pady=(10, 5))
        if not self._screen_off_available():
            self.btn_screen.set_enabled(False)
            self.btn_screen.set_text("Экран (только Windows)", "🖥")
        self.btn_sound = RoundedButton(
            service, "Звуковой сигнал", command=self.play_sound_signal,
            width=self._btn_w, height=self._btn_h_sm, radius=10,
            bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
            hover_bg=t["ACCENT"], theme=t,
            font=("Segoe UI", 9), icon="🔔")
        self.btn_sound.pack(padx=10, pady=(0, 10))

        self._section(parent, "ЛОВУШКИ (ВСЕ КАМЕРЫ)")
        traps = tk.Frame(parent, bg=t["BG_CARD"])
        traps.pack(fill="x", pady=(4, 10))
        self.btn_photo_trap = RoundedButton(
            traps, "Фотоловушка: ВЫКЛ", command=self.toggle_photo_trap,
            width=self._btn_w, height=self._btn_h, radius=10,
            bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
            hover_bg=t["ACCENT"], theme=t,
            font=("Segoe UI", 10, "bold"), icon="📷")
        self.btn_photo_trap.pack(padx=10, pady=(10, 3))
        self.photo_status_label = tk.Label(
            traps, text="○  Неактивна", bg=t["BG_CARD"], fg=t["FG_MUTED"],
            font=("Segoe UI", 8), anchor="w")
        self.photo_status_label.pack(fill="x", padx=16, pady=(0, 6))
        self.btn_video_trap = RoundedButton(
            traps, "Видеоловушка: ВЫКЛ", command=self.toggle_video_trap,
            width=self._btn_w, height=self._btn_h, radius=10,
            bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
            hover_bg=t["ACCENT"], theme=t,
            font=("Segoe UI", 10, "bold"), icon="🎥")
        self.btn_video_trap.pack(padx=10, pady=(3, 3))
        self.video_trap_status_label = tk.Label(
            traps, text="○  Неактивна", bg=t["BG_CARD"], fg=t["FG_MUTED"],
            font=("Segoe UI", 8), anchor="w")
        self.video_trap_status_label.pack(fill="x", padx=16, pady=(0, 10))

        self._section(parent, "МИКРОФОН")
        audio_card = tk.Frame(parent, bg=t["BG_CARD"])
        audio_card.pack(fill="x", pady=(4, 10))
        audio_row = tk.Frame(audio_card, bg=t["BG_CARD"])
        audio_row.pack(fill="x", padx=12, pady=8)
        tk.Label(audio_row, text="🎤", bg=t["BG_CARD"],
                 fg=t["FG_SECONDARY"],
                 font=("Segoe UI", 9)).pack(side="left")
        self.audio_meter = tk.Canvas(audio_row, height=8, bg=t["BG_INPUT"],
                                     highlightthickness=0, bd=0)
        self.audio_meter.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.audio_meter.bind("<Configure>",
                              lambda e: self._draw_audio_meter())
        if not AUDIO_AVAILABLE:
            tk.Label(audio_card,
                     text="⚠  pyaudio не установлен",
                     bg=t["BG_CARD"], fg=t["WARNING"],
                     font=("Segoe UI", 8), anchor="w"
                     ).pack(fill="x", padx=12, pady=(0, 8))

        self._section(parent, "СТАТУС")
        status_card = tk.Frame(parent, bg=t["BG_CARD"])
        status_card.pack(fill="x", pady=(4, 10))
        row = tk.Frame(status_card, bg=t["BG_CARD"])
        row.pack(fill="x", padx=12, pady=8)
        self.rec_dot = tk.Canvas(row, width=12, height=12, bg=t["BG_CARD"],
                                 highlightthickness=0)
        self.rec_dot.pack(side="left", padx=(0, 8))
        self.rec_dot_id = self.rec_dot.create_oval(2, 2, 10, 10,
                                                   fill=t["FG_MUTED"],
                                                   outline="")
        self.status_label = tk.Label(row, text="Ожидание", bg=t["BG_CARD"],
                                     fg=t["FG_PRIMARY"],
                                     font=("Segoe UI", 10, "bold"),
                                     anchor="w")
        self.status_label.pack(side="left", fill="x", expand=True)
        self.auto_progress = ttk.Progressbar(
            status_card, style="Accent.Horizontal.TProgressbar",
            mode="determinate", maximum=100)
        self.stats_label = tk.Label(
            status_card,
            text="Камер: 0   •   Срабатываний: 0   •   Записано: 00:00",
            bg=t["BG_CARD"], fg=t["FG_MUTED"],
            font=("Consolas", 8), anchor="w")
        self.stats_label.pack(fill="x", padx=12, pady=(0, 8))

    def _section(self, parent, text):
        tk.Label(parent, text=text, bg=self.theme["BG_MAIN"],
                 fg=self.theme["FG_MUTED"], font=("Segoe UI", 8, "bold"),
                 anchor="w").pack(fill="x", pady=(4, 0))

    # ============ АРХИВ: UI ============
    def _build_archive_panel(self, parent):
        t = self.theme
        self.archive_panel = tk.Frame(parent, bg=t["BG_MAIN"])

        left = tk.Frame(self.archive_panel, bg=t["BG_MAIN"])
        left.pack(side="left", fill="both", expand=True)

        switcher = tk.Frame(left, bg=t["BG_CARD"])
        switcher.pack(fill="x", pady=(0, 6))
        self.archive_tab_photo_btn = RoundedButton(
            switcher, "📷  Фото",
            command=lambda: self._archive_switch_tab("photo"),
            width=110, height=30, radius=8,
            bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
            hover_bg=t["ACCENT"], theme=t, font=("Segoe UI", 9, "bold"))
        self.archive_tab_photo_btn.pack(side="left", padx=(6, 4), pady=5)
        self.archive_tab_video_btn = RoundedButton(
            switcher, "🎥  Видео",
            command=lambda: self._archive_switch_tab("video"),
            width=110, height=30, radius=8,
            bg=t["ACCENT"], fg="#ffffff",
            hover_bg=t["ACCENT_HOVER"], theme=t,
            font=("Segoe UI", 9, "bold"))
        self.archive_tab_video_btn.pack(side="left", padx=4, pady=5)
        self.archive_tab_info = tk.Label(
            switcher, text="", bg=t["BG_CARD"], fg=t["FG_MUTED"],
            font=("Segoe UI", 9))
        self.archive_tab_info.pack(side="right", padx=10)

        border = tk.Frame(left, bg=t["BORDER"])
        border.pack(fill="both", expand=True)
        self.archive_display = tk.Frame(border, bg="#000000")
        self.archive_display.pack(fill="both", expand=True, padx=2, pady=2)

        self.archive_video_frame = tk.Frame(self.archive_display,
                                            bg="#000000")
        self.archive_video_frame.pack(fill="both", expand=True)
        self.archive_video_label = tk.Label(
            self.archive_video_frame, bg="#000000", bd=0,
            text="🎥  Видеоархив\n\nВыберите файл из списка справа",
            fg=t["FG_MUTED"], font=("Segoe UI", 12), justify="center")
        self.archive_video_label.pack(fill="both", expand=True)
        self.archive_video_label.bind(
            "<Double-Button-1>", lambda e: self._archive_open_fullscreen())

        self.archive_photo_frame = tk.Frame(self.archive_display,
                                            bg="#000000")
        self.archive_photo_label = tk.Label(
            self.archive_photo_frame, bg="#000000", bd=0,
            text="📷  Фотоархив\n\nВыберите миниатюру снизу",
            fg=t["FG_MUTED"], font=("Segoe UI", 12), justify="center")
        self.archive_photo_label.pack(fill="both", expand=True)
        self.archive_photo_label.bind(
            "<Double-Button-1>", lambda e: self._archive_open_fullscreen())

        player_h = 115 if self._compact_mode else 125
        self.archive_player_bar = tk.Frame(left, bg=t["BG_CARD"],
                                           height=player_h)
        self.archive_player_bar.pack(fill="x", pady=(6, 0))
        self.archive_player_bar.pack_propagate(False)

        self.archive_progress_canvas = tk.Canvas(
            self.archive_player_bar, height=20, bg=t["BG_INPUT"],
            highlightthickness=0, bd=0)
        self.archive_progress_canvas.pack(fill="x", padx=8, pady=(6, 2))
        self.archive_progress_canvas.bind("<Button-1>",
                                          self._on_archive_seek_start)
        self.archive_progress_canvas.bind("<B1-Motion>",
                                          self._on_archive_seek_drag)
        self.archive_progress_canvas.bind("<ButtonRelease-1>",
                                          self._on_archive_seek_end)
        self.archive_progress_canvas.configure(cursor="hand2")

        info_row = tk.Frame(self.archive_player_bar, bg=t["BG_CARD"])
        info_row.pack(fill="x", padx=8, pady=(0, 2))
        self.archive_time_label = tk.Label(
            info_row, text="00:00 / 00:00   •   1.00×",
            bg=t["BG_CARD"], fg=t["FG_SECONDARY"],
            font=("Consolas", 9), anchor="w")
        self.archive_time_label.pack(side="left")
        self.archive_frame_label = tk.Label(
            info_row, text="кадр 0", bg=t["BG_CARD"], fg=t["FG_MUTED"],
            font=("Consolas", 8))
        self.archive_frame_label.pack(side="right")

        row1 = tk.Frame(self.archive_player_bar, bg=t["BG_CARD"])
        row1.pack(fill="x", padx=8, pady=(2, 2))

        left_btns = tk.Frame(row1, bg=t["BG_CARD"])
        left_btns.pack(side="left", fill="x", expand=True)

        bh = 28 if self._compact_mode else 30
        self.archive_btn_play = RoundedButton(
            left_btns, "▶", command=self.archive_toggle_play,
            width=38, height=bh, radius=7,
            bg=t["SUCCESS_DARK"], fg="#ffffff",
            hover_bg=t["SUCCESS"], theme=t,
            font=("Segoe UI", 10, "bold"))
        self.archive_btn_play.pack(side="left", padx=(0, 2))

        RoundedButton(left_btns, "⏹", command=self.archive_stop,
                      width=38, height=bh, radius=7,
                      bg=t["DANGER_DARK"], fg="#ffffff",
                      hover_bg=t["DANGER_HOVER"], theme=t,
                      font=("Segoe UI", 10, "bold")
                      ).pack(side="left", padx=2)

        RoundedButton(left_btns, "⏮", command=self.archive_go_start,
                      width=38, height=bh, radius=7,
                      bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
                      hover_bg=t["ACCENT"], theme=t,
                      font=("Segoe UI", 10, "bold")
                      ).pack(side="left", padx=2)

        RoundedButton(left_btns, "◀|",
                      command=lambda: self.archive_step_frame(-1),
                      width=38, height=bh, radius=7,
                      bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
                      hover_bg=t["ACCENT"], theme=t,
                      font=("Segoe UI", 9, "bold")
                      ).pack(side="left", padx=2)

        RoundedButton(left_btns, "|▶",
                      command=lambda: self.archive_step_frame(1),
                      width=38, height=bh, radius=7,
                      bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
                      hover_bg=t["ACCENT"], theme=t,
                      font=("Segoe UI", 9, "bold")
                      ).pack(side="left", padx=2)

        for label, delta in [("⏪30", -30), ("⏪10", -10), ("⏪5", -5),
                             ("5⏩", 5), ("10⏩", 10), ("30⏩", 30)]:
            w_ = 52 if abs(delta) >= 10 else 46
            RoundedButton(left_btns, label,
                          command=lambda d=delta: self.archive_seek_relative(d),
                          width=w_, height=bh, radius=7,
                          bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
                          hover_bg=t["ACCENT"], theme=t,
                          font=("Segoe UI", 8)
                          ).pack(side="left", padx=2)

        right_btns = tk.Frame(row1, bg=t["BG_CARD"])
        right_btns.pack(side="right")
        RoundedButton(right_btns, "💾",
                      command=self.archive_save_frame,
                      width=42, height=bh, radius=7,
                      bg=t["SUCCESS_DARK"], fg="#ffffff",
                      hover_bg=t["SUCCESS"], theme=t,
                      font=("Segoe UI", 10, "bold")
                      ).pack(side="left", padx=2)
        RoundedButton(right_btns, "⛶",
                      command=self._archive_open_fullscreen,
                      width=42, height=bh, radius=7,
                      bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
                      hover_bg=t["ACCENT"], theme=t,
                      font=("Segoe UI", 11, "bold")
                      ).pack(side="left", padx=2)
        RoundedButton(right_btns, "🔄",
                      command=self.archive_refresh_list,
                      width=42, height=bh, radius=7,
                      bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
                      hover_bg=t["ACCENT"], theme=t,
                      font=("Segoe UI", 10)
                      ).pack(side="left", padx=2)

        row2 = tk.Frame(self.archive_player_bar, bg=t["BG_CARD"])
        row2.pack(fill="x", padx=8, pady=(2, 6))
        tk.Label(row2, text="Скорость:", bg=t["BG_CARD"],
                 fg=t["FG_SECONDARY"],
                 font=("Segoe UI", 8)).pack(side="left", padx=(0, 4))
        self.archive_speed_btns = {}
        spd_w = 52 if self._compact_mode else 58
        for spd, label in [(0.25, "0.25×"), (0.5, "0.5×"),
                           (1.0, "1×"), (1.5, "1.5×"),
                           (2.0, "2×"), (4.0, "4×")]:
            btn = RoundedButton(
                row2, label,
                command=lambda s=spd: self.archive_set_speed(s),
                width=spd_w, height=bh - 2, radius=7,
                bg=t["ACCENT"] if abs(spd - 1.0) < 1e-6
                else t["BG_INPUT"],
                fg="#ffffff" if abs(spd - 1.0) < 1e-6
                else t["FG_PRIMARY"],
                hover_bg=t["ACCENT_HOVER"], theme=t,
                font=("Segoe UI", 8, "bold"))
            btn.pack(side="left", padx=2)
            self.archive_speed_btns[spd] = btn

        right_w = 400 if not self._narrow_mode else 320
        right = tk.Frame(self.archive_panel, bg=t["BG_MAIN"], width=right_w)
        right.pack(side="right", fill="y", padx=(10, 0))
        right.pack_propagate(False)

        hdr = tk.Frame(right, bg=t["BG_CARD"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="📁  ФАЙЛЫ", bg=t["BG_CARD"], fg=t["FG_PRIMARY"],
                 font=("Segoe UI", 10, "bold")).pack(side="left",
                                                     padx=10, pady=8)
        self.archive_count_label = tk.Label(
            hdr, text="0 файлов", bg=t["BG_CARD"], fg=t["FG_MUTED"],
            font=("Segoe UI", 8))
        self.archive_count_label.pack(side="right", padx=10)

        search_frame = tk.Frame(right, bg=t["BG_CARD"])
        search_frame.pack(fill="x", padx=8, pady=(0, 6))
        tk.Label(search_frame, text="🔍", bg=t["BG_CARD"],
                 fg=t["FG_SECONDARY"], font=("Segoe UI", 10)
                 ).pack(side="left", padx=(0, 4))
        self.archive_search_var = tk.StringVar()
        self._search_timer = None
        self.archive_search_var.trace_add("write",
                                          self._on_search_changed)
        tk.Entry(search_frame, textvariable=self.archive_search_var,
                 bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
                 insertbackground=t["FG_PRIMARY"], bd=0, relief="flat",
                 font=("Segoe UI", 9), highlightthickness=1,
                 highlightbackground=t["BORDER"], highlightcolor=t["ACCENT"]
                 ).pack(side="left", fill="x", expand=True, ipady=4)

        filters = tk.Frame(right, bg=t["BG_CARD"])
        filters.pack(fill="x", padx=8, pady=(0, 6))
        tk.Label(filters, text="CAM:", bg=t["BG_CARD"],
                 fg=t["FG_SECONDARY"], font=("Segoe UI", 8)
                 ).pack(side="left")
        self.archive_cam_var = tk.StringVar(value="Все")
        self.archive_cam_combo = ttk.Combobox(
            filters, textvariable=self.archive_cam_var, state="readonly",
            style="Dark.TCombobox", font=("Segoe UI", 8), width=6,
            values=["Все"])
        self.archive_cam_combo.pack(side="left", padx=(4, 6))
        self.archive_cam_combo.bind(
            "<<ComboboxSelected>>", lambda e: self.archive_refresh_list())
        tk.Label(filters, text="Тип:", bg=t["BG_CARD"],
                 fg=t["FG_SECONDARY"], font=("Segoe UI", 8)
                 ).pack(side="left")
        self.archive_type_var = tk.StringVar(value="Все")
        self.archive_type_combo = ttk.Combobox(
            filters, textvariable=self.archive_type_var, state="readonly",
            style="Dark.TCombobox", font=("Segoe UI", 8), width=10,
            values=["Все", "Ручные (Запись)", "Авто (Видео)"])
        self.archive_type_combo.pack(side="left", padx=(4, 0))
        self.archive_type_combo.bind(
            "<<ComboboxSelected>>", lambda e: self.archive_refresh_list())

        self.archive_list_wrap = tk.Frame(right, bg=t["BORDER"])
        self.archive_list_wrap.pack(fill="both", expand=True,
                                    padx=8, pady=(0, 6))
        inner = tk.Frame(self.archive_list_wrap, bg=t["BG_INPUT"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.archive_listbox = tk.Listbox(
            inner, bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
            selectbackground=t["ACCENT"], selectforeground="#ffffff",
            bd=0, relief="flat", highlightthickness=0,
            font=("Consolas", 8), activestyle="none", selectmode="browse")
        self.archive_listbox.pack(side="left", fill="both", expand=True,
                                  padx=4, pady=4)
        scroll = tk.Scrollbar(inner, command=self.archive_listbox.yview)
        scroll.pack(side="right", fill="y")
        self.archive_listbox.config(yscrollcommand=scroll.set)
        self.archive_listbox.bind("<<ListboxSelect>>",
                                  lambda e: self._archive_on_video_select())
        self.archive_listbox.bind("<Double-Button-1>",
                                  lambda e: self.archive_play_selected())
        self.archive_listbox.bind("<Return>",
                                  lambda e: self.archive_play_selected())
        self.archive_listbox.bind("<Delete>",
                                  lambda e: self.archive_delete_selected())

        self.archive_thumbs_wrap = tk.Frame(right, bg=t["BORDER"])
        thumbs_inner = tk.Frame(self.archive_thumbs_wrap, bg=t["BG_INPUT"])
        thumbs_inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.archive_thumbs_canvas = tk.Canvas(
            thumbs_inner, bg=t["BG_INPUT"], highlightthickness=0, bd=0)
        self.archive_thumbs_canvas.pack(side="left", fill="both",
                                        expand=True, padx=4, pady=4)
        thumbs_scroll = tk.Scrollbar(
            thumbs_inner, command=self.archive_thumbs_canvas.yview)
        thumbs_scroll.pack(side="right", fill="y")
        self.archive_thumbs_canvas.config(yscrollcommand=thumbs_scroll.set)
        self.archive_thumbs_inner = tk.Frame(self.archive_thumbs_canvas,
                                             bg=t["BG_INPUT"])
        self.archive_thumbs_window = self.archive_thumbs_canvas.create_window(
            (0, 0), window=self.archive_thumbs_inner, anchor="nw")
        self.archive_thumbs_inner.bind(
            "<Configure>",
            lambda e: self.archive_thumbs_canvas.configure(
                scrollregion=self.archive_thumbs_canvas.bbox("all")))
        self.archive_thumbs_canvas.bind(
            "<Configure>",
            lambda e: self.archive_thumbs_canvas.itemconfig(
                self.archive_thumbs_window, width=e.width))

        actions = tk.Frame(right, bg=t["BG_CARD"])
        actions.pack(fill="x", padx=8, pady=(0, 8))
        self.archive_play_button = RoundedButton(
            actions, "▶  Воспроизвести",
            command=self.archive_play_selected,
            width=200, height=34, radius=9,
            bg=t["SUCCESS_DARK"], fg="#ffffff",
            hover_bg=t["SUCCESS"], theme=t,
            font=("Segoe UI", 9, "bold"))
        self.archive_play_button.pack(fill="x", pady=(0, 5))
        row2b = tk.Frame(actions, bg=t["BG_CARD"])
        row2b.pack(fill="x")
        RoundedButton(row2b, "📂 Папка", command=self.archive_open_folder,
                      width=100, height=30, radius=8,
                      bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
                      hover_bg=t["ACCENT"], theme=t, font=("Segoe UI", 8)
                      ).pack(side="left", fill="x", expand=True, padx=(0, 3))
        RoundedButton(row2b, "🗑 Удалить",
                      command=self.archive_delete_selected,
                      width=100, height=30, radius=8,
                      bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
                      hover_bg=t["DANGER"], theme=t, font=("Segoe UI", 8)
                      ).pack(side="left", fill="x", expand=True, padx=(3, 0))

        # [OPT-11] 15 fps
        self.root.after(66, self._archive_tick)

    def _on_search_changed(self, *_):
        if self._search_timer is not None:
            try:
                self.root.after_cancel(self._search_timer)
            except Exception:
                pass
        self._search_timer = self.root.after(300, self.archive_refresh_list)

    # ============ АРХИВ: ЛОГИКА ============
    def toggle_archive_view(self):
        self.archive_view = not getattr(self, "archive_view", False)
        if self.archive_view:
            self.view_btn.set_enabled(False)
            self.archive_btn.set_text("Камеры", "◀")
            self.archive_btn.set_colors(bg=self.theme["ACCENT"],
                                        hover=self.theme["ACCENT_HOVER"],
                                        fg="#ffffff")
            self._show_archive_panel()
        else:
            self.view_btn.set_enabled(True)
            self.archive_btn.set_text("Архив", "▶")
            self.archive_btn.set_colors(bg=self.theme["BG_INPUT"],
                                        hover=self.theme["ACCENT"],
                                        fg=self.theme["FG_PRIMARY"])
            self._hide_archive_panel()

    def _show_archive_panel(self):
        if hasattr(self, "cameras_panel"):
            self.cameras_panel.pack_forget()
        if hasattr(self, "right_panel"):
            self.right_panel.pack_forget()
        if hasattr(self, "archive_panel"):
            self.archive_panel.pack(fill="both", expand=True)
            self.archive_refresh_list()
        self._archive_switch_tab(self.archive_tab, force=True)

    def _hide_archive_panel(self):
        if hasattr(self, "archive_panel"):
            self.archive_panel.pack_forget()
        try:
            self.archive_stop()
        except Exception:
            pass
        if hasattr(self, "cameras_panel"):
            self.cameras_panel.pack(side="left", fill="both", expand=True)
        if hasattr(self, "right_panel"):
            self.right_panel.pack(side="right", fill="y", padx=(10, 0))

    def _archive_switch_tab(self, tab, force=False):
        if (tab == self.archive_tab and not force
                and hasattr(self, "archive_tab_info")):
            txt = self.archive_tab_info.cget("text")
            if txt:
                return
        self.archive_tab = tab
        t = self.theme
        if tab == "video":
            self.archive_tab_video_btn.set_colors(
                bg=t["ACCENT"], hover=t["ACCENT_HOVER"], fg="#ffffff")
            self.archive_tab_photo_btn.set_colors(
                bg=t["BG_INPUT"], hover=t["ACCENT"], fg=t["FG_PRIMARY"])
            self.archive_video_frame.pack(fill="both", expand=True)
            self.archive_photo_frame.pack_forget()
            self.archive_list_wrap.pack(fill="both", expand=True,
                                        padx=8, pady=(0, 6),
                                        before=self.archive_play_button.master)
            self.archive_thumbs_wrap.pack_forget()
            self.archive_player_bar.pack(fill="x", pady=(6, 0))
            self.archive_play_button.set_text("▶  Воспроизвести", "▶")
            self.archive_play_button.set_colors(
                bg=t["SUCCESS_DARK"], hover=t["SUCCESS"])
            self.archive_tab_info.config(text="Режим: видео")
        else:
            self.archive_tab_photo_btn.set_colors(
                bg=t["ACCENT"], hover=t["ACCENT_HOVER"], fg="#ffffff")
            self.archive_tab_video_btn.set_colors(
                bg=t["BG_INPUT"], hover=t["ACCENT"], fg=t["FG_PRIMARY"])
            self.archive_photo_frame.pack(fill="both", expand=True)
            self.archive_video_frame.pack_forget()
            self.archive_thumbs_wrap.pack(fill="both", expand=True,
                                          padx=8, pady=(0, 6),
                                          before=self.archive_play_button.master)
            self.archive_list_wrap.pack_forget()
            self.archive_player_bar.pack_forget()
            self.archive_play_button.set_text("⛶  Открыть", "⛶")
            self.archive_play_button.set_colors(
                bg=t["ACCENT"], hover=t["ACCENT_HOVER"])
            self.archive_tab_info.config(text="Режим: фото")
        self.archive_refresh_list()

    def archive_refresh_list(self):
        if not hasattr(self, "archive_listbox"):
            return
        files = []
        photos = []
        for idx in sorted(self.channels.keys()):
            ch = self.channels[idx]
            vdir = ch.videos_folder
            if os.path.isdir(vdir):
                try:
                    with os.scandir(vdir) as it:
                        for e in it:
                            if not e.is_file():
                                continue
                            name = e.name
                            if not name.lower().endswith(VIDEO_EXTS):
                                continue
                            try:
                                st = e.stat()
                            except OSError:
                                continue
                            files.append({
                                "path": e.path, "name": name, "cam": idx,
                                "size": st.st_size, "mtime": st.st_mtime,
                                "is_auto": name.startswith("Видео"),
                                "is_manual": name.startswith("Запись"),
                            })
                except Exception:
                    pass
            pdir = ch.photos_folder
            if os.path.isdir(pdir):
                try:
                    with os.scandir(pdir) as it:
                        for e in it:
                            if not e.is_file():
                                continue
                            name = e.name
                            if not name.lower().endswith(PHOTO_EXTS):
                                continue
                            try:
                                st = e.stat()
                            except OSError:
                                continue
                            photos.append({
                                "path": e.path, "name": name, "cam": idx,
                                "size": st.st_size, "mtime": st.st_mtime,
                            })
                except Exception:
                    pass
        cams = sorted({f["cam"] for f in files}
                      | {p["cam"] for p in photos})
        values = ["Все"] + [f"#{c}" for c in cams]
        self.archive_cam_combo["values"] = values
        if self.archive_cam_var.get() not in values:
            self.archive_cam_var.set("Все")
        q = self.archive_search_var.get().strip().lower()
        cam_filter = self.archive_cam_var.get()
        type_filter = self.archive_type_var.get()
        filtered = []
        for f in files:
            if cam_filter != "Все" and f"#{f['cam']}" != cam_filter:
                continue
            if type_filter == "Ручные (Запись)" and not f["is_manual"]:
                continue
            if type_filter == "Авто (Видео)" and not f["is_auto"]:
                continue
            if q and q not in f["name"].lower():
                continue
            filtered.append(f)
        filtered.sort(key=lambda x: x["mtime"], reverse=True)
        self.archive_files = filtered
        filtered_photos = []
        for p in photos:
            if cam_filter != "Все" and f"#{p['cam']}" != cam_filter:
                continue
            if q and q not in p["name"].lower():
                continue
            filtered_photos.append(p)
        filtered_photos.sort(key=lambda x: x["mtime"], reverse=True)
        self.archive_photo_files = filtered_photos
        if self.archive_tab == "video":
            self.archive_listbox.delete(0, tk.END)
            for f in filtered:
                dt = datetime.fromtimestamp(f["mtime"]).strftime(
                    "%d.%m %H:%M:%S")
                mb = f["size"] / (1024 * 1024)
                kind = ("АВТО" if f["is_auto"]
                        else ("РУЧН" if f["is_manual"] else "???"))
                line = (f" CAM#{f['cam']} [{kind}] {dt} {mb:6.1f}MB "
                        f"{f['name']}")
                self.archive_listbox.insert(tk.END, line)
            self.archive_count_label.config(
                text=f"{len(filtered)} видео • {len(filtered_photos)} фото")
        else:
            self._build_photo_thumbs()
            self.archive_count_label.config(
                text=f"{len(filtered_photos)} фото • {len(filtered)} видео")

    # [OPT-5] Ограничение видимых миниатюр + семафор
    def _build_photo_thumbs(self):
        if not hasattr(self, "archive_thumbs_inner"):
            return
        t = self.theme
        for w in self.archive_thumbs_inner.winfo_children():
            w.destroy()
        self.archive_photo_cells.clear()
        self._thumb_futures.clear()
        thumbs_per_row = 3
        thumb_w, thumb_h = 120, 90
        files = self.archive_photo_files
        if not files:
            tk.Label(self.archive_thumbs_inner,
                     text="📷  Фотографий не найдено",
                     bg=t["BG_INPUT"], fg=t["FG_MUTED"],
                     font=("Segoe UI", 10)
                     ).grid(row=0, column=0, columnspan=thumbs_per_row,
                            padx=10, pady=20, sticky="w")
            return
        visible = files[:MAX_VISIBLE_THUMBS]
        for i, info in enumerate(visible):
            r = i // thumbs_per_row
            c = i % thumbs_per_row
            self._build_thumb_cell(info, r, c, thumb_w, thumb_h)
        if len(files) > MAX_VISIBLE_THUMBS:
            row = len(visible) // thumbs_per_row + 1
            tk.Label(self.archive_thumbs_inner,
                     text=(f"... ещё {len(files) - MAX_VISIBLE_THUMBS} фото "
                           f"(используйте фильтр CAM или поиск)"),
                     bg=t["BG_INPUT"], fg=t["FG_MUTED"],
                     font=("Segoe UI", 9)
                     ).grid(row=row, column=0, columnspan=thumbs_per_row,
                            padx=10, pady=10, sticky="w")

    def _build_thumb_cell(self, info, row, col, tw, th):
        t = self.theme
        path = info["path"]
        cell = tk.Frame(self.archive_thumbs_inner, bg=t["BORDER"],
                        highlightthickness=0)
        cell.grid(row=row, column=col, padx=2, pady=2, sticky="nsew")
        inner = tk.Frame(cell, bg=t["BG_CARD"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        img_holder = tk.Label(inner, bg="#000000", text="…",
                              fg=t["FG_MUTED"], width=tw, height=th)
        img_holder.pack(fill="both", expand=True)
        dt = datetime.fromtimestamp(info["mtime"]).strftime("%d.%m %H:%M:%S")
        meta = tk.Label(
            inner, text=f"CAM#{info['cam']} {dt}\n{info['name'][:26]}",
            bg=t["BG_CARD"], fg=t["FG_SECONDARY"],
            font=("Consolas", 7), justify="left", anchor="w")
        meta.pack(fill="x", padx=3, pady=(0, 3))
        self.archive_photo_cells[path] = (img_holder, inner, meta)
        for widget in (img_holder, meta, inner, cell):
            widget.bind("<Button-1>",
                        lambda e, p=path: self._archive_select_photo(p))
            widget.bind("<Double-Button-1>",
                        lambda e, p=path: self._archive_open_fullscreen(p))
            widget.configure(cursor="hand2")
        self._schedule_thumb(path, tw, th)

    def _schedule_thumb(self, path, tw, th):
        if path in self._thumb_futures:
            return

        def _task():
            with self._thumb_semaphore:
                self._load_photo_thumb_async(path, tw, th)
        self._thumb_futures[path] = self._thumb_pool.submit(_task)

    def _load_photo_thumb_async(self, path, tw, th):
        thumb = None
        key = (path, tw, th)
        cached = _PHOTO_THUMB_CACHE.get(key)
        if cached is not None:
            thumb = cached
        if thumb is None:
            try:
                img = Image.open(path)
                img = img.convert("RGB")
                img.thumbnail((tw * 2, th * 2), _LANCZOS)
                thumb = img.copy()
                _PHOTO_THUMB_CACHE.put(key, thumb)
            except Exception as e:
                log.debug(f"thumb error {path}: {e}")
                thumb = None
        if thumb is None:
            return
        self.ui_call(self._apply_photo_thumb, path, thumb, tw, th)

    def _apply_photo_thumb(self, path, pil_img, tw, th):
        if path not in self.archive_photo_cells:
            return
        holder, _, _ = self.archive_photo_cells[path]
        try:
            if not holder.winfo_exists():
                return
            base = pil_img.copy()
            base.thumbnail((tw, th), _LANCZOS)
            canvas = Image.new("RGB", (tw, th), (0, 0, 0))
            canvas.paste(base, ((tw - base.width) // 2,
                                (th - base.height) // 2))
            imgtk = ImageTk.PhotoImage(canvas)
            holder.imgtk = imgtk
            holder.config(image=imgtk, text="")
        except Exception as e:
            log.debug(f"apply thumb error: {e}")

    def _archive_select_photo(self, path):
        self.archive_selected_photo = path
        for p, (holder, inner, _) in self.archive_photo_cells.items():
            try:
                inner.config(bg=self.theme["BG_CARD"])
            except Exception:
                pass
        if path in self.archive_photo_cells:
            _, inner, _ = self.archive_photo_cells[path]
            try:
                inner.config(bg=self.theme["ACCENT"])
            except Exception:
                pass
        self._archive_show_photo_big(path)

    def _archive_show_photo_big(self, path):
        try:
            img = Image.open(path).convert("RGB")
        except Exception as e:
            Toast.show(self.root, f"Не открыть фото: {e}",
                       kind="error", theme=self.theme)
            return
        w = self.archive_photo_label.winfo_width() or 800
        h = self.archive_photo_label.winfo_height() or 500
        if w < 50:
            w = 800
        if h < 50:
            h = 500
        cp = img.copy()
        cp.thumbnail((w, h), _LANCZOS)
        imgtk = ImageTk.PhotoImage(cp)
        self.archive_photo_label.imgtk = imgtk
        self.archive_photo_label.config(image=imgtk, text="")

    def _archive_on_video_select(self):
        path = self._archive_selected_path()
        if not path:
            return
        if not ARCHIVE_PLAYER_AVAILABLE:
            self.archive_video_label.config(
                image="",
                text=("⚠  Плеер недоступен.\n\n"
                      "pip install imageio imageio-ffmpeg"),
                fg=self.theme["WARNING"], font=("Segoe UI", 11))
            return
        frame = self._get_video_preview(path)
        if frame is None:
            self.archive_video_label.config(
                image="",
                text=f"⚠  Не прочитать кадр: {os.path.basename(path)}",
                fg=self.theme["WARNING"])
            return
        if self.archive_playing:
            return
        try:
            w = self.archive_video_label.winfo_width() or 800
            h = self.archive_video_label.winfo_height() or 500
            if w > 10 and h > 10:
                disp = self._fit_frame(frame, w, h)
                rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
                imgtk = ImageTk.PhotoImage(Image.fromarray(rgb, mode="RGB"))
                self.archive_video_label.imgtk = imgtk
                self.archive_video_label.config(image=imgtk, text="")
        except Exception:
            pass
        fps, dur = _video_meta(path)
        m, s = divmod(int(dur), 60)
        self.archive_time_label.config(
            text=(f"00:00 / {m:02d}:{s:02d}   •   1.00×   •   "
                  f"{os.path.basename(path)}"))

    def _get_video_preview(self, path):
        if not ARCHIVE_PLAYER_AVAILABLE:
            return None
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        cached = _VIDEO_PREVIEW_CACHE.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        frame = _get_first_frame(path)
        if frame is not None:
            try:
                small = cv2.resize(frame, (320, 180))
            except Exception:
                small = frame
            _VIDEO_PREVIEW_CACHE.put(path, (mtime, small))
            return small
        return None

    def _archive_selected_path(self):
        if not hasattr(self, "archive_listbox"):
            return None
        sel = self.archive_listbox.curselection()
        if not sel:
            return None
        idx = sel[0]
        if 0 <= idx < len(self.archive_files):
            return self.archive_files[idx]["path"]
        return None

    def archive_play_selected(self):
        if self.archive_tab == "photo":
            self._archive_open_fullscreen()
            return
        path = self._archive_selected_path()
        if not path:
            Toast.show(self.root, "Выберите файл",
                       kind="warning", theme=self.theme)
            return
        self.archive_open(path)

    def archive_open(self, path):
        self.archive_stop()
        if not ARCHIVE_PLAYER_AVAILABLE:
            msg = ("Плеер недоступен.\n\n"
                   "Установите: pip install imageio imageio-ffmpeg")
            Toast.show(self.root, "Плеер недоступен", kind="error",
                       theme=self.theme, duration=6000)
            self.archive_video_label.config(
                image="", text=f"🎥  {msg}", fg=self.theme["WARNING"],
                font=("Segoe UI", 11))
            return
        if not os.path.exists(path):
            Toast.show(self.root, "Файл не найден", kind="error",
                       theme=self.theme)
            return
        self.archive_current_path = path
        self.archive_playing = True
        self.archive_paused = False
        self.archive_pause_event.set()
        self.archive_position = 0.0
        self.archive_frame_count = 0
        self.archive_frame_index = 0
        self.archive_speed = 1.0
        with self.archive_seek_lock:
            self.archive_seek_request = None
        self.archive_saved_frame_path = None
        self.archive_step_frame = 0
        fps, dur = _video_meta(path)
        self.archive_fps = fps if fps > 0 else 30.0
        self.archive_duration = dur
        self.archive_worker_stop.clear()
        self.archive_worker_thread = threading.Thread(
            target=self._archive_worker, args=(path, 0.0, 1.0),
            daemon=True, name="ArchivePlayer")
        self.archive_worker_thread.start()
        self.archive_btn_play.set_text("⏸", "⏸")
        self.archive_btn_play.set_colors(
            bg=self.theme["WARNING"], hover=self.theme["WARNING"])
        self.file_info_label.config(
            text=f"🎬  Архив: {os.path.basename(path)}")
        self._archive_update_speed_ui()

    def _archive_worker(self, path, start_sec=0.0, speed=1.0):
        fps = self.archive_fps if self.archive_fps > 0 else 30.0
        base_delay = 1.0 / fps

        def _run_stream(start_seconds, spd):
            frame_gen = _video_frames_iter(path, start_seconds)
            base_idx = int(start_seconds * fps)
            local_count = base_idx
            last = time.time()
            got_any = False
            try:
                for bgr in frame_gen:
                    got_any = True
                    if self.archive_worker_stop.is_set():
                        break
                    with self.archive_seek_lock:
                        seek_req = self.archive_seek_request
                        if seek_req is not None:
                            self.archive_seek_request = None
                    if seek_req is not None:
                        return ("seek", seek_req)
                    if abs(self.archive_speed - spd) > 1e-6:
                        return ("speed", None)
                    with self.archive_step_lock:
                        step = self.archive_step_frame
                        self.archive_step_frame = 0
                    if step != 0:
                        if step > 0:
                            for _ in range(abs(step) - 1):
                                try:
                                    next(frame_gen)
                                    local_count += 1
                                except StopIteration:
                                    break
                        if step < 0:
                            new_sec = max(0.0, (local_count + step) / fps)
                            return ("seek_abs", new_sec)
                    if not self.archive_pause_event.is_set():
                        while not self.archive_pause_event.is_set():
                            if self.archive_worker_stop.is_set():
                                return ("stop", None)
                            with self.archive_seek_lock:
                                seek_req = self.archive_seek_request
                                if seek_req is not None:
                                    self.archive_seek_request = None
                                    return ("seek", seek_req)
                            self.archive_pause_event.wait(timeout=0.1)
                        last = time.time()
                    if self.archive_worker_stop.is_set():
                        break
                    with self.archive_frame_lock:
                        self.archive_current_frame = bgr
                    local_count += 1
                    self.archive_frame_index = local_count
                    self.archive_frame_count = local_count
                    self.archive_position = local_count / fps
                    delay = base_delay / max(0.05, spd)
                    now = time.time()
                    el = now - last
                    if el < delay:
                        time.sleep(delay - el)
                    last = time.time()
            finally:
                try:
                    if hasattr(frame_gen, "close"):
                        frame_gen.close()
                except Exception:
                    pass
            if not got_any and not self.archive_worker_stop.is_set():
                return ("empty", None)
            return ("eof", None)

        current_sec = float(start_sec)
        current_speed = float(speed)
        restart_count = 0
        last_restart_time = time.time()

        while not self.archive_worker_stop.is_set():
            if time.time() - last_restart_time > 5.0:
                restart_count = 0
            if restart_count > 200:
                log.warning("[ARCHIVE] Слишком много перезапусков, стоп")
                break
            restart_count += 1
            last_restart_time = time.time()
            result = _run_stream(current_sec, current_speed)
            if self.archive_worker_stop.is_set():
                break
            kind, val = result
            if kind == "seek":
                current_sec = max(0.0, min(
                    val if val is not None else self.archive_position,
                    self.archive_duration or 1e9))
                current_speed = self.archive_speed
                continue
            if kind == "speed":
                current_sec = self.archive_position
                current_speed = self.archive_speed
                continue
            if kind == "seek_abs":
                current_sec = max(0.0, val)
                current_speed = self.archive_speed
                continue
            if kind == "empty":
                self.ui_call(Toast.show, self.root,
                             "Не удалось прочитать видео.\n"
                             "Проверьте imageio-ffmpeg или системный ffmpeg.",
                             "error", 5000, self.theme)
                break
            if kind == "stop":
                break
            if kind == "eof":
                break

        self.ui_call(self._on_archive_playback_finished)

    def _on_archive_playback_finished(self):
        if self.archive_playing:
            self.archive_playing = False
            self.archive_btn_play.set_text("▶", "▶")
            self.archive_btn_play.set_colors(
                bg=self.theme["SUCCESS_DARK"], hover=self.theme["SUCCESS"])

    def archive_toggle_play(self):
        if not self.archive_current_path:
            self.archive_play_selected()
            return
        self.archive_paused = not self.archive_paused
        if self.archive_paused:
            self.archive_pause_event.clear()
            self.archive_btn_play.set_text("▶", "▶")
        else:
            self.archive_pause_event.set()
            self.archive_btn_play.set_text("⏸", "⏸")
            self.archive_last_ts = time.time()

    def archive_stop(self):
        self.archive_worker_stop.set()
        self.archive_pause_event.set()
        with self.archive_seek_lock:
            self.archive_seek_request = None
        self.archive_playing = False
        self.archive_paused = False
        self.archive_position = 0.0
        self.archive_frame_count = 0
        self.archive_frame_index = 0
        self.archive_current_path = None
        self.archive_speed = 1.0
        self.archive_saved_frame_path = None
        with self.archive_frame_lock:
            self.archive_current_frame = None
        if hasattr(self, "archive_btn_play"):
            self.archive_btn_play.set_text("▶", "▶")
            self.archive_btn_play.set_colors(
                bg=self.theme["SUCCESS_DARK"], hover=self.theme["SUCCESS"])
        if hasattr(self, "archive_video_label"):
            self.archive_video_label.config(
                image="", text="🎥  Видеоархив\n\nВыберите файл справа",
                fg=self.theme["FG_MUTED"])
        if hasattr(self, "archive_time_label"):
            self.archive_time_label.config(text="00:00 / 00:00   •   1.00×")
        if hasattr(self, "archive_frame_label"):
            self.archive_frame_label.config(text="кадр 0")
        self._archive_update_speed_ui()

    def _archive_seek_to(self, seconds):
        if not self.archive_current_path:
            return
        seconds = max(0.0, seconds)
        if self.archive_duration > 0:
            seconds = min(seconds, self.archive_duration)
        with self.archive_seek_lock:
            self.archive_seek_request = seconds
        self.archive_position = seconds
        self.archive_frame_index = int(seconds * self.archive_fps)

    def archive_seek_relative(self, delta_sec):
        if not self.archive_current_path:
            return
        target = self.archive_position + delta_sec
        if self.archive_duration > 0:
            target = max(0.0, min(target, self.archive_duration))
        else:
            target = max(0.0, target)
        self._archive_seek_to(target)

    def archive_go_start(self):
        if not self.archive_current_path:
            return
        self._archive_seek_to(0.0)

    def archive_set_speed(self, speed):
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            return
        speed = max(0.1, min(16.0, speed))
        self.archive_speed = speed
        self._archive_update_speed_ui()

    def _archive_update_speed_ui(self):
        if not hasattr(self, "archive_speed_btns"):
            return
        t = self.theme
        for spd, btn in self.archive_speed_btns.items():
            if abs(spd - self.archive_speed) < 1e-6:
                btn.set_colors(bg=t["ACCENT"], hover=t["ACCENT_HOVER"],
                               fg="#ffffff")
            else:
                btn.set_colors(bg=t["BG_INPUT"], hover=t["ACCENT"],
                               fg=t["FG_PRIMARY"])

    def archive_step_frame(self, direction):
        if not self.archive_current_path:
            return
        if not self.archive_paused:
            self.archive_paused = True
            self.archive_pause_event.clear()
            self.archive_btn_play.set_text("▶", "▶")
            time.sleep(0.05)
        with self.archive_step_lock:
            self.archive_step_frame = int(direction)

    def archive_save_frame(self):
        if not self.archive_current_path:
            Toast.show(self.root, "Видео не открыто",
                       kind="warning", theme=self.theme)
            return
        with self.archive_frame_lock:
            frame = self.archive_current_frame
            frame = None if frame is None else frame.copy()
        if frame is None:
            Toast.show(self.root, "Нет кадра",
                       kind="warning", theme=self.theme)
            return
        path = self.archive_current_path
        cam_idx = None
        parts = os.path.normpath(path).split(os.sep)
        for part in parts:
            if part.startswith("Camera_"):
                try:
                    cam_idx = int(part.split("_", 1)[1])
                except (IndexError, ValueError):
                    cam_idx = None
                break
        if cam_idx is not None and cam_idx in self.channels:
            photos_dir = self.channels[cam_idx].photos_folder
        else:
            photos_dir = self.photos_folder
        try:
            os.makedirs(photos_dir, exist_ok=True)
        except Exception as e:
            Toast.show(self.root, f"Не создать папку: {e}",
                       kind="error", theme=self.theme)
            return
        base = os.path.splitext(os.path.basename(path))[0]
        pos = self.archive_position
        mm = int(pos // 60)
        ss = int(pos % 60)
        ms = int((pos - int(pos)) * 1000)
        fname = f"frame_{base}_{mm:02d}m{ss:02d}s{ms:03d}ms.jpg"
        fpath = os.path.join(photos_dir, fname)
        try:
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            if not ok:
                raise RuntimeError("imencode failed")
            with open(fpath, "wb") as f:
                f.write(buf.tobytes())
            self.archive_saved_frame_path = fpath
            Toast.show(self.root, f"💾 Кадр сохранён: {fname}",
                       kind="success", theme=self.theme, duration=2500)
            self.log(f"Сохранён кадр → {fpath}")
            self._update_counters_ui()
        except Exception as e:
            Toast.show(self.root, f"Ошибка: {e}",
                       kind="error", theme=self.theme)

    def _on_archive_seek_start(self, event):
        if not self.archive_current_path or self.archive_duration <= 0:
            return
        self.archive_slider_dragging = True
        self._update_slider_pos_from_event(event)

    def _on_archive_seek_drag(self, event):
        if not self.archive_slider_dragging:
            return
        self._update_slider_pos_from_event(event)

    def _on_archive_seek_end(self, event):
        if not self.archive_slider_dragging:
            return
        self.archive_slider_dragging = False
        if self.archive_duration <= 0:
            return
        c = self.archive_progress_canvas
        w = c.winfo_width()
        if w <= 0:
            return
        x = max(0, min(event.x, w))
        target = (x / w) * self.archive_duration
        self._archive_seek_to(target)

    def _update_slider_pos_from_event(self, event):
        c = self.archive_progress_canvas
        w = c.winfo_width()
        if w <= 0 or self.archive_duration <= 0:
            return
        x = max(0, min(event.x, w))
        self.archive_slider_pos = (x / w) * self.archive_duration
        self._draw_progress_bar()

    def _draw_progress_bar(self):
        if not hasattr(self, "archive_progress_canvas"):
            return
        c = self.archive_progress_canvas
        try:
            c.delete("all")
            w = c.winfo_width()
            h = c.winfo_height()
        except tk.TclError:
            return
        if w < 4:
            return
        t = self.theme
        c.create_rectangle(0, 4, w, h - 4, fill=t["BG_INPUT"], outline="")
        pos = (self.archive_slider_pos
               if self.archive_slider_dragging else self.archive_position)
        frac = 0.0
        if self.archive_duration > 0:
            frac = min(1.0, pos / self.archive_duration)
        fw = int(w * frac)
        if fw > 0:
            c.create_rectangle(0, 4, fw, h - 4, fill=t["ACCENT"], outline="")
        cx = max(5, min(w - 5, fw))
        c.create_oval(cx - 6, h // 2 - 6, cx + 6, h // 2 + 6,
                      fill=t["FG_PRIMARY"], outline=t["ACCENT"], width=2)

    def archive_open_folder(self):
        if self.archive_tab == "photo" and self.archive_selected_photo:
            path = self.archive_selected_photo
        else:
            path = self._archive_selected_path()
        folder = os.path.dirname(path) if path else self.output_path
        try:
            if IS_WINDOWS:
                os.startfile(folder)
            elif IS_MACOS:
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            Toast.show(self.root, f"Не открыть папку: {e}",
                       kind="error", theme=self.theme)

    def archive_delete_selected(self):
        if self.archive_tab == "photo":
            path = self.archive_selected_photo
        else:
            path = self._archive_selected_path()
        if not path:
            Toast.show(self.root, "Выберите файл",
                       kind="warning", theme=self.theme)
            return
        if not messagebox.askyesno(
                "Удаление", f"Удалить файл?\n\n{os.path.basename(path)}"):
            return
        try:
            if os.path.abspath(path) == os.path.abspath(
                    self.archive_current_path or ""):
                self.archive_stop()
            os.remove(path)
            for key in list(_PHOTO_THUMB_CACHE._data.keys()):
                if key[0] == path:
                    _PHOTO_THUMB_CACHE.pop(key, None)
            _VIDEO_PREVIEW_CACHE.pop(path, None)
            _VIDEO_META_CACHE.pop(path, None)
            if path == self.archive_selected_photo:
                self.archive_selected_photo = None
                self.archive_photo_label.config(image="",
                                                text="📷  Фотоархив")
            Toast.show(self.root, "Файл удалён",
                       kind="success", theme=self.theme)
            self.archive_refresh_list()
            self._update_counters_ui()
        except Exception as e:
            Toast.show(self.root, f"Не удалось удалить: {e}",
                       kind="error", theme=self.theme)

    def _archive_open_fullscreen(self, path=None):
        if path is None:
            if self.archive_tab == "photo":
                path = self.archive_selected_photo
            else:
                path = self._archive_selected_path()
        if not path:
            Toast.show(self.root, "Ничего не выбрано",
                       kind="warning", theme=self.theme)
            return
        is_video = path.lower().endswith(VIDEO_EXTS)
        if self.archive_preview_window is not None:
            try:
                self.archive_preview_window.destroy()
            except Exception:
                pass
        self.archive_preview_window = tk.Toplevel(self.root)
        win = self.archive_preview_window
        win.title(("🎥  " if is_video else "📷  ") + os.path.basename(path))
        win.configure(bg="#000000")
        try:
            win.attributes("-fullscreen", True)
        except Exception:
            win.geometry("1200x800")
        display = tk.Label(win, bg="#000000", bd=0)
        display.pack(fill="both", expand=True)
        info = tk.Label(win, text=os.path.basename(path), bg="#000000",
                        fg="#cccccc", font=("Consolas", 10))
        info.place(relx=0.5, rely=1.0, anchor="s", y=-10)

        state = {
            "running": True,
            "paused": False,
            "frame": None,
            "lock": threading.Lock(),
        }
        pause_event = threading.Event()
        pause_event.set()

        def close(_e=None):
            state["running"] = False
            self.archive_preview_playing = False
            pause_event.set()
            try:
                win.destroy()
            except Exception:
                pass
            self.archive_preview_window = None

        win.bind("<Escape>", close)
        win.bind("<Double-Button-1>", close)
        win.bind("<Button-3>", close)
        win.protocol("WM_DELETE_WINDOW", close)

        if not is_video:
            try:
                img = Image.open(path).convert("RGB")
            except Exception as e:
                Toast.show(self.root, f"Не открыть фото: {e}",
                           kind="error", theme=self.theme)
                close()
                return

            def render():
                if not state["running"] or not win.winfo_exists():
                    return
                w = display.winfo_width() or win.winfo_width() or 1200
                h = display.winfo_height() or win.winfo_height() or 800
                cp = img.copy()
                cp.thumbnail((w, h), _LANCZOS)
                imgtk = ImageTk.PhotoImage(cp)
                display.imgtk = imgtk
                display.config(image=imgtk, text="")
            win.after(50, render)
            win.bind("<Configure>", lambda e: render())
            return

        if not ARCHIVE_PLAYER_AVAILABLE:
            display.config(
                text="Плеер недоступен\n\n"
                     "pip install imageio imageio-ffmpeg",
                fg="#ff6666", font=("Segoe UI", 14))
            return

        self.archive_preview_playing = True

        def reader():
            fps = 30.0
            try:
                fps, _ = _video_meta(path)
                if fps <= 0:
                    fps = 30.0
            except Exception:
                pass
            delay = 1.0 / fps
            last = time.time()
            got_any = False
            try:
                for bgr in _video_frames_iter(path):
                    got_any = True
                    if not state["running"]:
                        break
                    try:
                        if not win.winfo_exists():
                            break
                    except tk.TclError:
                        break
                    pause_event.wait()
                    if not state["running"]:
                        break
                    with state["lock"]:
                        state["frame"] = bgr
                    now = time.time()
                    el = now - last
                    if el < delay:
                        time.sleep(delay - el)
                    last = time.time()
            except Exception as e:
                log.debug(f"[ARCHIVE] fullscreen reader error: {e}")
            if not got_any and state["running"]:
                def _err():
                    try:
                        display.config(text="Не удалось прочитать видео",
                                       fg="#ff6666", font=("Segoe UI", 14))
                    except Exception:
                        pass
                try:
                    win.after(0, _err)
                except Exception:
                    pass

        def render():
            if not state["running"]:
                return
            try:
                if not win.winfo_exists():
                    return
            except tk.TclError:
                return
            with state["lock"]:
                frame = (None if state["frame"] is None
                         else state["frame"].copy())
            if frame is not None:
                w = display.winfo_width() or 1200
                h = display.winfo_height() or 800
                fr = frame.shape[1] / frame.shape[0]
                tr = w / h if h > 0 else 1
                if fr > tr:
                    nw, nh = w, int(w / fr)
                else:
                    nh, nw = h, int(h * fr)
                disp = cv2.resize(frame, (max(1, nw), max(1, nh)))
                rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
                imgtk = ImageTk.PhotoImage(Image.fromarray(rgb, mode="RGB"))
                display.imgtk = imgtk
                display.config(image=imgtk, text="")
            win.after(33, render)

        def toggle_pause(_e=None):
            state["paused"] = not state["paused"]
            if state["paused"]:
                pause_event.clear()
            else:
                pause_event.set()

        win.bind("<space>", toggle_pause)
        win.bind("<Button-1>", toggle_pause)
        threading.Thread(target=reader, daemon=True,
                         name="FullscreenVideo").start()
        win.after(50, render)

    def _archive_tick(self):
        if (getattr(self, "archive_view", False)
                and hasattr(self, "archive_video_label")
                and self.archive_tab == "video"):
            with self.archive_frame_lock:
                frame = self.archive_current_frame
                if frame is not None:
                    frame = frame.copy()
            if frame is not None:
                try:
                    w = self.archive_video_label.winfo_width() or 800
                    h = self.archive_video_label.winfo_height() or 500
                    if w > 10 and h > 10:
                        disp = self._fit_frame(frame, w, h)
                        rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
                        imgtk = ImageTk.PhotoImage(
                            Image.fromarray(rgb, mode="RGB"))
                        self.archive_video_label.imgtk = imgtk
                        self.archive_video_label.config(image=imgtk, text="")
                except Exception:
                    pass
            try:
                self._draw_progress_bar()
            except Exception:
                pass
            try:
                def fmt(s):
                    s = int(max(0, s))
                    m, sec = divmod(s, 60)
                    return f"{m:02d}:{sec:02d}"
                pos = (self.archive_slider_pos
                       if self.archive_slider_dragging
                       else self.archive_position)
                name = (os.path.basename(self.archive_current_path)
                        if self.archive_current_path else "")
                self.archive_time_label.config(
                    text=(f"{fmt(pos)} / {fmt(self.archive_duration)}"
                          f"   •   {self.archive_speed:.2f}×"
                          f"   •   {name}"))
                self.archive_frame_label.config(
                    text=f"кадр {self.archive_frame_index}")
            except Exception:
                pass
        try:
            self.root.after(66, self._archive_tick)
        except tk.TclError:
            pass

    # ============ ГОРЯЧИЕ КЛАВИШИ ============
    def bind_hotkeys(self):
        def is_main_focused():
            try:
                focused = self.root.focus_get()
            except Exception:
                return False
            if focused is None:
                return False
            try:
                top = focused.winfo_toplevel()
            except Exception:
                return False
            return top == self.root

        def _archive_key(event):
            if not (getattr(self, "archive_view", False)
                    and self.archive_tab == "video"
                    and self.archive_current_path):
                return False
            ks = event.keysym
            if ks == "Left":
                self.archive_seek_relative(-5)
                return True
            if ks == "Right":
                self.archive_seek_relative(5)
                return True
            if ks == "Prior":
                self.archive_seek_relative(-30)
                return True
            if ks == "Next":
                self.archive_seek_relative(30)
                return True
            if ks == "Home":
                self.archive_go_start()
                return True
            if ks in ("comma", "less"):
                self.archive_set_speed(max(0.1, self.archive_speed - 0.25))
                return True
            if ks in ("period", "greater"):
                self.archive_set_speed(min(16.0, self.archive_speed + 0.25))
                return True
            if ks in ("k", "K"):
                self.archive_save_frame()
                return True
            return False

        def hotkey_space(event):
            if is_main_focused():
                if self._screen_is_off:
                    self._wake_screen()
                    return "break"
                if getattr(self, "archive_view", False):
                    self.archive_toggle_play()
                    return "break"
                if self._active_manual_recordings:
                    self.stop_recording_all()
                else:
                    self.start_recording_all()
                return "break"

        def hotkey_p(event):
            if is_main_focused() and not self._screen_is_off:
                self.toggle_photo_trap()
                return "break"

        def hotkey_v(event):
            if is_main_focused() and not self._screen_is_off:
                self.toggle_video_trap()
                return "break"

        def hotkey_s(event):
            if is_main_focused():
                self.open_settings()
                return "break"

        def hotkey_c(event):
            if is_main_focused() and not self._screen_is_off:
                self.switch_to_next_camera()
                return "break"

        def hotkey_g(event):
            if is_main_focused():
                self.toggle_grid_view()
                return "break"

        def hotkey_t(event):
            if is_main_focused():
                self.take_test_photo()
                return "break"

        def hotkey_l(event):
            if is_main_focused():
                if self._screen_is_off:
                    self._wake_screen()
                else:
                    self.turn_screen_off()
                return "break"

        def hotkey_b(event):
            if is_main_focused() and not self._screen_is_off:
                self.play_sound_signal()
                return "break"

        def hotkey_a(event):
            if is_main_focused() and not self._screen_is_off:
                self.toggle_archive_view()
                return "break"

        def hotkey_enter(event):
            if is_main_focused() and getattr(self, "archive_view", False):
                self._archive_open_fullscreen()
                return "break"

        def make_key(ks=None):
            def handler(event):
                if is_main_focused() and _archive_key(event):
                    return "break"
            return handler

        def hotkey_f11(event):
            if is_main_focused():
                self.toggle_fullscreen()
                return "break"

        def hotkey_esc(event):
            if is_main_focused():
                if self._fullscreen_active:
                    self._exit_fullscreen()
                else:
                    self.root.focus_set()

        self.root.bind("<space>", hotkey_space)
        self.root.bind("<KeyPress-p>", hotkey_p)
        self.root.bind("<KeyPress-P>", hotkey_p)
        self.root.bind("<KeyPress-v>", hotkey_v)
        self.root.bind("<KeyPress-V>", hotkey_v)
        self.root.bind("<KeyPress-s>", hotkey_s)
        self.root.bind("<KeyPress-S>", hotkey_s)
        self.root.bind("<KeyPress-c>", hotkey_c)
        self.root.bind("<KeyPress-C>", hotkey_c)
        self.root.bind("<KeyPress-g>", hotkey_g)
        self.root.bind("<KeyPress-G>", hotkey_g)
        self.root.bind("<KeyPress-t>", hotkey_t)
        self.root.bind("<KeyPress-T>", hotkey_t)
        self.root.bind("<KeyPress-l>", hotkey_l)
        self.root.bind("<KeyPress-L>", hotkey_l)
        self.root.bind("<KeyPress-b>", hotkey_b)
        self.root.bind("<KeyPress-B>", hotkey_b)
        self.root.bind("<KeyPress-a>", hotkey_a)
        self.root.bind("<KeyPress-A>", hotkey_a)
        self.root.bind("<Return>", hotkey_enter)
        self.root.bind("<KeyPress-k>", make_key())
        self.root.bind("<KeyPress-K>", make_key())
        self.root.bind("<Left>", make_key())
        self.root.bind("<Right>", make_key())
        self.root.bind("<Home>", make_key())
        self.root.bind("<Prior>", make_key())
        self.root.bind("<Next>", make_key())
        self.root.bind("<comma>", make_key())
        self.root.bind("<period>", make_key())
        self.root.bind("<F11>", hotkey_f11)
        self.root.bind("<Escape>", hotkey_esc)
        self.root.bind_all("<Any-KeyPress>", self._wake_screen, add="+")
        self.root.bind_all("<Any-Button>", self._wake_screen, add="+")
        self.root.bind_all("<Motion>", self._wake_screen, add="+")

    def take_test_photo(self):
        if not self.channels:
            Toast.show(self.root, "Нет камер",
                       kind="warning", theme=self.theme)
            return
        base = self.output_path
        self.log(f"Тестовый снимок → {base}")
        saved = 0
        errors = []
        for idx, ch in self.channels.items():
            frame = ch.get_frame()
            if frame is None:
                errors.append(f"CAM#{idx}: нет кадра")
                continue
            # [OPT-2] get_frame теперь возвращает ссылку — копируем перед записью
            path = ch.save_photo_now(frame.copy())
            if path:
                saved += 1
            else:
                errors.append(f"CAM#{idx}: не сохранено")
        if saved:
            Toast.show(self.root,
                       f"📷 Тестовый снимок: {saved} → {base}",
                       kind="success", theme=self.theme, duration=3000)
        else:
            detail = "; ".join(errors) if errors else "нет кадров"
            Toast.show(self.root, f"❌ Не сохранилось: {detail}",
                       kind="error", theme=self.theme, duration=4000)

    def _auto_enable_traps(self):
        if not self.auto_enable_traps:
            return
        if not self.channels:
            self.root.after(1000, self._auto_enable_traps)
            return
        if not self.photo_trap_enabled:
            self.toggle_photo_trap()
        if not self.video_trap_enabled:
            self.toggle_video_trap()

    def toggle_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.theme = THEMES[self.theme_name]
        self.save_config()
        for w in self.root.winfo_children():
            w.destroy()
        self.root.configure(bg=self.theme["BG_MAIN"])
        self._setup_styles()
        self.create_widgets()
        self.bind_hotkeys()
        self._refresh_traps_ui()
        self._update_counters_ui()
        self._rebuild_grid()
        if self.archive_view:
            self._show_archive_panel()
        Toast.show(self.root,
                   f"Тема: {'тёмная' if self.theme_name == 'dark' else 'светлая'}",
                   kind="info", theme=self.theme)

    def _refresh_traps_ui(self):
        if self.photo_trap_enabled:
            self.btn_photo_trap.set_text("Фотоловушка: ВКЛ", "📷")
            self.btn_photo_trap.set_colors(bg=self.theme["ACCENT"],
                                           hover=self.theme["ACCENT_HOVER"])
            self.photo_status_label.config(text="●  Активна (все камеры)",
                                           fg=self.theme["SUCCESS"])
        else:
            self.btn_photo_trap.set_text("Фотоловушка: ВЫКЛ", "📷")
            self.btn_photo_trap.set_colors(bg=self.theme["BG_INPUT"],
                                           hover=self.theme["ACCENT"])
            self.photo_status_label.config(text="○  Неактивна",
                                           fg=self.theme["FG_MUTED"])
        if self.video_trap_enabled:
            self.btn_video_trap.set_text("Видеоловушка: ВКЛ", "🎥")
            self.btn_video_trap.set_colors(bg=self.theme["ACCENT"],
                                           hover=self.theme["ACCENT_HOVER"])
            self.video_trap_status_label.config(text="●  Активна (все камеры)",
                                                fg=self.theme["SUCCESS"])
        else:
            self.btn_video_trap.set_text("Видеоловушка: ВЫКЛ", "🎥")
            self.btn_video_trap.set_colors(bg=self.theme["BG_INPUT"],
                                           hover=self.theme["ACCENT"])
            self.video_trap_status_label.config(text="○  Неактивна",
                                                fg=self.theme["FG_MUTED"])

    # [OPT-4] Снижена частота вызова из heartbeat
    def _update_counters_ui(self):
        total_photos = 0
        total_videos = 0
        for ch in self.channels.values():
            n_photos, n_videos = ch.count_files_on_disk()
            total_photos += n_photos
            total_videos += n_videos
        try:
            self.photo_count_label.config(text=str(total_photos))
            self.video_count_label.config(text=str(total_videos))
        except Exception:
            pass

    # ============ [OPT-10] ЕДИНЫЙ HEARTBEAT ============
    def _ui_heartbeat(self):
        try:
            self._animate_rec_indicator_once()
        except tk.TclError:
            return
        except Exception as e:
            log.debug(f"heartbeat indicator: {e}")
        try:
            self._update_recording_timer_once()
        except tk.TclError:
            return
        except Exception as e:
            log.debug(f"heartbeat timer: {e}")
        try:
            self.root.after(500, self._ui_heartbeat)
        except tk.TclError:
            pass

    def _animate_rec_indicator_once(self):
        try:
            active = self._any_recording()
            if active:
                self._blink_state = not self._blink_state
                color = (self.theme["DANGER"] if self._blink_state
                         else self.theme["DANGER_DARK"])
                self.rec_dot.itemconfig(self.rec_dot_id, fill=color)
            else:
                self.rec_dot.itemconfig(self.rec_dot_id,
                                        fill=self.theme["FG_MUTED"])
        except tk.TclError:
            raise

    def _any_recording(self):
        return any(ch.is_active_recording() for ch in self.channels.values())

    def _update_recording_timer_once(self):
        active_recs = [ch for ch in self.channels.values()
                       if ch.is_active_recording()
                       and ch.recording_started_at]
        if active_recs:
            target = min(active_recs, key=lambda c: c.recording_started_at)
            elapsed = datetime.now() - target.recording_started_at
            total = int(elapsed.total_seconds())
            h, rem = divmod(total, 3600)
            m, s = divmod(rem, 60)
            self.timer_label.config(
                text=(f"⏱  {h:02d}:{m:02d}:{s:02d}   •   "
                      f"FPS: {target.actual_fps:.1f}   •   "
                      f"CAM#{target.index}   •   Кадров: "
                      f"{target.recording_frames}   "
                      f"(записей: {len(active_recs)})"))
        else:
            display_ch = self.channels.get(self.display_index)
            fps = display_ch.actual_fps if display_ch else 0.0
            cam_txt = f"CAM#{display_ch.index}" if display_ch else "—"
            self.timer_label.config(
                text=f"⏱  00:00:00   •   FPS: {fps:.1f}   •   {cam_txt}")
        motion_recs = [ch for ch in self.channels.values()
                       if ch.motion_recording and ch.recording_started_at]
        if motion_recs:
            tgt = motion_recs[0]
            elapsed = (datetime.now()
                       - tgt.recording_started_at).total_seconds()
            pct = min(100, elapsed / max(
                1, getattr(self, "motion_duration_sec", 900)) * 100)
            self.auto_progress["value"] = pct
        else:
            self.auto_progress["value"] = 0
        n_cams = len(self.channels)
        total_motions = sum(ch.motion_count
                            for ch in self.channels.values())
        total_sec = sum(ch.total_recorded()
                        for ch in self.channels.values())
        th, trem = divmod(int(total_sec), 3600)
        tm, ts = divmod(trem, 60)
        self.stats_label.config(
            text=(f"Камер: {n_cams}   •   Срабатываний: {total_motions}"
                  f"   •   Записано: {th:02d}:{tm:02d}:{ts:02d}"))
        # [OPT-4] Счётчики файлов обновляем раз в 5 сек
        now = time.time()
        if now - self._last_counters_update > COUNTERS_UPDATE_INTERVAL:
            self._last_counters_update = now
            self._update_counters_ui()

    def toggle_grid_view(self):
        self.grid_view = not self.grid_view
        self.view_btn.set_text("Сетка" if self.grid_view else "Одна", "")
        if self.grid_view:
            self.view_btn.set_colors(bg=self.theme["ACCENT"],
                                     hover=self.theme["ACCENT_HOVER"],
                                     fg="#ffffff")
        else:
            self.view_btn.set_colors(bg=self.theme["BG_INPUT"],
                                     hover=self.theme["ACCENT"],
                                     fg=self.theme["FG_PRIMARY"])
        self._rebuild_grid()
        self.save_config()
        Toast.show(
            self.root,
            "Сетка: все камеры" if self.grid_view else "Режим: одна камера",
            kind="info", theme=self.theme, duration=1200)

    def _rebuild_grid(self):
        if self._grid_frame is not None:
            try:
                self._grid_frame.destroy()
            except Exception:
                pass
            self._grid_frame = None
        try:
            self.video_label.pack_forget()
        except Exception:
            pass
        for lbl in self.camera_labels.values():
            try:
                lbl.destroy()
            except Exception:
                pass
        self.camera_labels.clear()
        if not self.grid_view:
            self.video_label = tk.Label(self.video_container, bg="#000000",
                                        bd=0,
                                        text="📷  Инициализация камер...",
                                        fg=self.theme["FG_MUTED"],
                                        font=("Segoe UI", 12))
            self.video_label.pack(fill="both", expand=True)
            return
        if not self.channels:
            self.video_label = tk.Label(self.video_container, bg="#000000",
                                        bd=0, text="📷  Камеры не найдены",
                                        fg=self.theme["FG_MUTED"],
                                        font=("Segoe UI", 12))
            self.video_label.pack(fill="both", expand=True)
            return
        keys = sorted(self.channels.keys())
        n = len(keys)
        cols, rows = self._grid_dimensions(n)
        grid = tk.Frame(self.video_container, bg="#000000")
        grid.pack(fill="both", expand=True)
        self._grid_frame = grid
        for i in range(rows):
            grid.rowconfigure(i, weight=1, uniform="cam")
        for j in range(cols):
            grid.columnconfigure(j, weight=1, uniform="cam")
        for pos, idx in enumerate(keys):
            r = pos // cols
            c = pos % cols
            cell = tk.Frame(grid, bg="#000000",
                            highlightthickness=1,
                            highlightbackground=self.theme["BORDER"])
            cell.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
            lbl = tk.Label(cell, bg="#000000", bd=0,
                           text=f"CAM#{idx}\n(нет кадра)",
                           fg=self.theme["FG_MUTED"],
                           font=("Segoe UI", 10))
            lbl.pack(fill="both", expand=True)
            lbl.bind("<Button-1>", lambda e, i=idx: self.switch_camera(i))
            self.camera_labels[idx] = lbl

    @staticmethod
    def _grid_dimensions(n):
        if n <= 1:
            return 1, 1
        if n == 2:
            return 2, 1
        if n <= 4:
            return 2, 2
        if n <= 6:
            return 3, 2
        if n <= 9:
            return 3, 3
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)
        return cols, rows

    def _update_display_loop(self):
        try:
            if not getattr(self, "archive_view", False):
                if self.grid_view:
                    self._update_grid_frames()
                else:
                    self._update_single_frame()
        except Exception as e:
            log.debug(f"display loop error: {e}")
        try:
            self.root.after(33, self._update_display_loop)
        except tk.TclError:
            pass

    def _update_single_frame(self):
        now = time.time()
        if now - self._last_single_update < 1.0 / SINGLE_UPDATE_FPS:
            return
        self._last_single_update = now
        ch = self.channels.get(self.display_index)
        if ch is None:
            return
        frame = ch.get_frame()
        if frame is None:
            return
        try:
            w = self.video_label.winfo_width() or 800
            h = self.video_label.winfo_height() or 500
            if w < 10:
                w = 800
            if h < 10:
                h = 500
            disp = self._fit_frame(frame, w, h)
            rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
            imgtk = ImageTk.PhotoImage(Image.fromarray(rgb, mode="RGB"))
            self.video_label.imgtk = imgtk
            self.video_label.config(image=imgtk, text="")
        except tk.TclError:
            pass
        except Exception:
            pass

    def _update_grid_frames(self):
        now = time.time()
        if now - self._last_grid_update < 1.0 / GRID_UPDATE_FPS:
            return
        self._last_grid_update = now
        if not self.camera_labels:
            return
        for idx, lbl in list(self.camera_labels.items()):
            ch = self.channels.get(idx)
            if ch is None:
                continue
            frame = ch.get_frame()
            if frame is None:
                continue
            try:
                if not lbl.winfo_exists():
                    continue
                w = lbl.winfo_width()
                h = lbl.winfo_height()
                if w < 40 or h < 40:
                    continue
                disp = self._fit_frame(frame, w, h)
                rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
                imgtk = ImageTk.PhotoImage(
                    Image.fromarray(rgb, mode="RGB"))
                lbl.imgtk = imgtk
                lbl.config(image=imgtk, text="")
            except tk.TclError:
                pass
            except Exception:
                pass

    @staticmethod
    def _fit_frame(frame, w, h):
        fr = frame.shape[1] / frame.shape[0]
        tr = w / h
        if fr > tr:
            nw, nh = w, int(w / fr)
        else:
            nh, nw = h, int(h * fr)
        return cv2.resize(frame, (max(1, nw), max(1, nh)),
                          interpolation=cv2.INTER_LINEAR)

    def log(self, message, kind="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] {message}")
        if kind == "error":
            log.error(message)
        elif kind == "warning":
            log.warning(message)

    # [OPT-12] Ленивый log-queue
    def _process_log_queue(self):
        try:
            while True:
                self.log_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self.root.after(500, self._process_log_queue)
        except tk.TclError:
            pass

    def _draw_audio_meter(self):
        if not hasattr(self, "audio_meter"):
            return
        try:
            t = self.theme
            self.audio_meter.delete("all")
            w = self.audio_meter.winfo_width()
            h = self.audio_meter.winfo_height()
            if w < 4:
                return
            self.audio_meter.create_rectangle(0, 0, w, h, fill=t["BG_INPUT"],
                                              outline="")
            lw = int(w * min(self.audio_level, 100) / 100)
            if lw > 0:
                color = (t["SUCCESS"] if self.audio_level < 70
                         else t["WARNING"] if self.audio_level < 90
                         else t["DANGER"])
                self.audio_meter.create_rectangle(0, 0, lw, h, fill=color,
                                                  outline="")
        except tk.TclError:
            pass

    def _set_status(self, text, color=None):
        color = color or self.theme["FG_PRIMARY"]
        try:
            if hasattr(self, "status_label"):
                self.status_label.config(text=text, fg=color)
            if hasattr(self, "header_status"):
                self.header_status.config(
                    text=f"●  {text}",
                    fg=(color if color != self.theme["FG_PRIMARY"]
                        else self.theme["FG_SECONDARY"]))
        except tk.TclError:
            pass

    # [OPT-8] grab вместо read, таймауты
    def scan_available_cameras(self, max_index=MAX_CAMERAS_SCAN, force=False):
        now = time.time()
        if (not force
                and self._cam_scan_cache
                and now - self._cam_scan_cache_time < CAMERA_SCAN_TTL):
            return list(self._cam_scan_cache)
        if IS_WINDOWS:
            apis = [cv2.CAP_DSHOW, cv2.CAP_MSMF]
        elif IS_LINUX:
            apis = [cv2.CAP_V4L2, cv2.CAP_ANY]
        else:
            apis = [cv2.CAP_ANY]

        def probe(idx):
            for api in apis:
                cap = None
                try:
                    cap = cv2.VideoCapture(idx, api)
                    if not cap.isOpened():
                        continue
                    if cap.grab():
                        return idx, True
                except Exception:
                    pass
                finally:
                    if cap is not None:
                        try:
                            cap.release()
                        except Exception:
                            pass
            return idx, False

        results = {}
        futures = {self._scan_pool.submit(probe, i): i
                   for i in range(max_index)}
        for fut in as_completed(futures, timeout=3.0):
            try:
                idx, ok = fut.result(timeout=1.0)
                results[idx] = ok
            except Exception:
                results[futures[fut]] = False
        available = sorted([i for i, ok in results.items() if ok])
        self._cam_scan_cache = list(available)
        self._cam_scan_cache_time = time.time()
        return available

    def initial_connect_all(self):
        self._set_status("Поиск камер...", self.theme["WARNING"])
        try:
            self.root.update_idletasks()
        except Exception:
            pass

        def worker():
            found = self.scan_available_cameras(force=True)
            self.ui_call(self._on_scan_complete, found)
        threading.Thread(target=worker, daemon=True,
                         name="InitialScan").start()

    def _on_scan_complete(self, found):
        found = sorted(found)
        self.available_cameras = found
        self.log(f"Найдено камер: {len(found)} → {found}")
        if not found:
            self._set_status("Камеры не найдены", self.theme["DANGER"])
            Toast.show(self.root, "Камеры не найдены",
                       kind="warning", theme=self.theme)
            self.root.after(3000, self.initial_connect_all)
            return
        for idx in found:
            if idx not in self.channels:
                self.channels[idx] = CameraChannel(self, idx)
        for idx in list(self.channels.keys()):
            if idx not in found:
                ch = self.channels[idx]
                self._control_pool.submit(ch.close)
                del self.channels[idx]
        self._restore_channel_counters()
        connected = 0
        open_futures = {self._open_pool.submit(ch.open): idx
                        for idx, ch in self.channels.items()}
        for fut in as_completed(open_futures):
            idx = open_futures[fut]
            try:
                if fut.result(timeout=3.0):
                    connected += 1
                    ch = self.channels[idx]
                    self.log(f"Камера #{idx} подключена → {ch.base_folder}")
            except Exception as e:
                self.log(f"Ошибка подключения CAM#{idx}: {e}", "error")
        if self.display_index not in self.channels:
            self.display_index = found[0] if found else None
        self._set_status(f"Подключено: {connected}/{len(found)}",
                         self.theme["SUCCESS"])
        if connected > 1:
            Toast.show(self.root, f"Подключено камер: {connected}",
                       kind="success", theme=self.theme, duration=3500)
        elif connected == 1:
            Toast.show(self.root, "Подключена 1 камера",
                       kind="success", theme=self.theme)
        else:
            self._set_status("Не подключено ни одной камеры",
                             self.theme["DANGER"])
            self.root.after(3000, self.initial_connect_all)
        self._rebuild_grid()
        self._update_counters_ui()

    def on_photo_saved(self, index, filepath):
        self.file_info_label.config(text=f"📁  CAM#{index} фото: {filepath}")
        self.photo_status_label.config(
            text=f"●  CAM#{index}: ДВИЖЕНИЕ — фото",
            fg=self.theme["SUCCESS"])
        self.root.after(1500, lambda: self.photo_status_label.config(
            text=("●  Активна (все камеры)" if self.photo_trap_enabled
                  else "○  Неактивна"),
            fg=(self.theme["SUCCESS"] if self.photo_trap_enabled
                else self.theme["FG_MUTED"])))
        Toast.show(self.root, f"📷 CAM#{index}: снимок сохранён",
                   kind="success", theme=self.theme, duration=1500)
        self._update_counters_ui()

    def on_motion_recording_started(self, index):
        ch = self.channels.get(index)
        if ch is None:
            return
        filename = os.path.basename(ch.current_video_path)
        self.file_info_label.config(
            text=f"📁  CAM#{index} авто: {filename}")
        self.rec_type_label.config(text=f"Тип записи: АВТО (CAM#{index})",
                                   fg=self.theme["WARNING"])
        self._set_status(f"Авто-запись (CAM#{index})", self.theme["WARNING"])
        Toast.show(self.root, f"🎥 CAM#{index}: запись начата",
                   kind="warning", theme=self.theme, duration=2000)
        self._update_buttons_state()
        self._update_counters_ui()

    def on_motion_recording_stopped(self, index):
        self._update_buttons_state()
        self._update_counters_ui()

    def on_recording_finished(self, index):
        ch = self.channels.get(index)
        if ch and ch.current_video_path:
            self.file_info_label.config(
                text=f"📁  CAM#{index}: {ch.current_video_path}")
        else:
            self.file_info_label.config(
                text=f"📁  CAM#{index}: файл сохранён")
        self.rec_type_label.config(text="Тип записи: нет",
                                   fg=self.theme["ACCENT"])
        self._update_buttons_state()
        self._update_counters_ui()
        self.save_config()

    def _update_buttons_state(self):
        any_rec = self._any_recording()
        any_online = any(ch.online for ch in self.channels.values())
        if any_rec:
            self.btn_stop.set_enabled(True)
            self.btn_stop.set_colors(bg=self.theme["DANGER_DARK"],
                                     hover=self.theme["DANGER_HOVER"],
                                     fg="#ffffff")
            self.btn_start.set_enabled(False)
            self.btn_settings.set_enabled(False)
        else:
            self.btn_stop.set_enabled(False)
            self.btn_stop.set_colors(bg=self.theme["DISABLED_BG"],
                                     hover=self.theme["DANGER_HOVER"],
                                     fg=self.theme["DISABLED_FG"])
            self.btn_start.set_enabled(any_online)
            self.btn_settings.set_enabled(True)

    def start_recording_all(self):
        if not self.channels:
            Toast.show(self.root, "Нет камер",
                       kind="warning", theme=self.theme)
            return

        def worker():
            started = 0
            futures = {
                self._control_pool.submit(ch.start_manual_recording): idx
                for idx, ch in self.channels.items() if ch.online}
            for fut in as_completed(futures):
                try:
                    if fut.result(timeout=3.0):
                        started += 1
                except Exception as e:
                    idx = futures[fut]
                    self.log(f"Ошибка старта CAM#{idx}: {e}", "error")
            self.ui_call(self._on_recordings_started, started)
        threading.Thread(target=worker, daemon=True,
                         name="StartRecAll").start()

    def _on_recordings_started(self, started):
        if started == 0:
            messagebox.showerror("Ошибка",
                                 "Не удалось начать запись.")
            return
        self._active_manual_recordings = True
        self.rec_type_label.config(
            text=f"Тип записи: РУЧНАЯ (все: {started})",
            fg=self.theme["SUCCESS"])
        self._set_status(f"Ручная запись: {started} камер",
                         self.theme["DANGER"])
        self._update_buttons_state()
        self._update_counters_ui()
        self.save_config()
        Toast.show(self.root, f"⏺  Запись на {started} камер(ах)",
                   kind="success", theme=self.theme, duration=2000)

    def stop_recording_all(self):
        def worker():
            stopped = 0
            futs = []
            for ch in self.channels.values():
                if ch.is_recording:
                    futs.append(
                        self._control_pool.submit(ch.stop_manual_recording))
                    stopped += 1
                if ch.motion_recording:
                    futs.append(
                        self._control_pool.submit(ch.stop_motion_recording))
                    stopped += 1
            for f in futs:
                try:
                    f.result(timeout=3.0)
                except Exception:
                    pass
            self.ui_call(self._on_recordings_stopped, stopped)
        threading.Thread(target=worker, daemon=True,
                         name="StopRecAll").start()

    def _on_recordings_stopped(self, stopped):
        self._active_manual_recordings = False
        self.rec_type_label.config(text="Тип записи: нет",
                                   fg=self.theme["ACCENT"])
        self._set_status("Запись остановлена", self.theme["FG_SECONDARY"])
        self._update_buttons_state()
        self._update_counters_ui()
        if stopped:
            Toast.show(self.root, f"Остановлено записей: {stopped}",
                       kind="info", theme=self.theme, duration=1800)

    def toggle_photo_trap(self):
        self.photo_trap_enabled = not self.photo_trap_enabled
        if self.photo_trap_enabled:
            self.btn_photo_trap.set_text("Фотоловушка: ВКЛ", "📷")
            self.btn_photo_trap.set_colors(bg=self.theme["ACCENT"],
                                           hover=self.theme["ACCENT_HOVER"])
            self.photo_status_label.config(text="●  Активна (все камеры)",
                                           fg=self.theme["SUCCESS"])
            for ch in self.channels.values():
                ch.prev_gray = None
                ch.bg_subtractor = None
                ch.bg_frames = 0
                ch.motion_confirm_counter = 0
                ch.last_photo_time = 0
                ch._motion_active = False
            Toast.show(self.root, "📷  Фотоловушка включена",
                       kind="info", theme=self.theme, duration=1800)
            self.root.after(700, self.take_test_photo)
        else:
            self.btn_photo_trap.set_text("Фотоловушка: ВЫКЛ", "📷")
            self.btn_photo_trap.set_colors(bg=self.theme["BG_INPUT"],
                                           hover=self.theme["ACCENT"])
            self.photo_status_label.config(text="○  Неактивна",
                                           fg=self.theme["FG_MUTED"])

    def toggle_video_trap(self):
        self.video_trap_enabled = not self.video_trap_enabled
        if self.video_trap_enabled:
            self.btn_video_trap.set_text("Видеоловушка: ВКЛ", "🎥")
            self.btn_video_trap.set_colors(bg=self.theme["ACCENT"],
                                           hover=self.theme["ACCENT_HOVER"])
            self.video_trap_status_label.config(
                text="●  Активна (все камеры)", fg=self.theme["SUCCESS"])
            for ch in self.channels.values():
                ch.prev_gray = None
                ch.bg_subtractor = None
                ch.bg_frames = 0
                ch.motion_confirm_counter = 0
                ch.last_video_time = 0
                ch._motion_active = False
            Toast.show(self.root, "🎥  Видеоловушка включена",
                       kind="info", theme=self.theme, duration=1800)
        else:
            self.btn_video_trap.set_text("Видеоловушка: ВЫКЛ", "🎥")
            self.btn_video_trap.set_colors(bg=self.theme["BG_INPUT"],
                                           hover=self.theme["ACCENT"])
            self.video_trap_status_label.config(text="○  Неактивна",
                                                fg=self.theme["FG_MUTED"])
            for ch in self.channels.values():
                if ch.motion_recording:
                    ch.stop_motion_recording()

    def switch_camera(self, index):
        if index not in self.channels:
            Toast.show(self.root, f"Камера #{index} недоступна",
                       kind="warning", theme=self.theme)
            return
        self.display_index = index
        if self.grid_view:
            self.grid_view = False
            self.view_btn.set_text("Одна", "")
            self.view_btn.set_colors(bg=self.theme["BG_INPUT"],
                                     hover=self.theme["ACCENT"],
                                     fg=self.theme["FG_PRIMARY"])
            self._rebuild_grid()
        Toast.show(self.root, f"Показ CAM#{index}",
                   kind="info", theme=self.theme, duration=1200)

    def switch_to_next_camera(self):
        if not self.channels:
            return
        keys = sorted(self.channels.keys())
        if self.display_index not in keys:
            self.switch_camera(keys[0])
            return
        pos = keys.index(self.display_index)
        nxt = keys[(pos + 1) % len(keys)]
        self.switch_camera(nxt)

    # ============ НАСТРОЙКИ ============
    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title(f"Настройки — {APP_NAME}")
        sw = self._screen_w
        sh = self._screen_h
        s_w = min(600, int(sw * 0.55))
        s_h = min(900, int(sh * 0.88))
        win.geometry(f"{s_w}x{s_h}")
        win.minsize(520, 500)
        win.configure(bg=self.theme["BG_MAIN"])
        win.transient(self.root)
        win.grab_set()
        t = self.theme
        header = tk.Frame(win, bg=t["BG_PANEL"], height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="⚙  Настройки", bg=t["BG_PANEL"],
                 fg=t["FG_PRIMARY"],
                 font=("Segoe UI", 13, "bold")).pack(side="left",
                                                     padx=16, pady=12)
        notebook = ttk.Notebook(win, style="Dark.TNotebook")
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        tab_main = tk.Frame(notebook, bg=t["BG_CARD"])
        notebook.add(tab_main, text="  Основные  ")
        folder_var = tk.StringVar(value=self.output_path)
        fps_var = tk.StringVar(value=str(self.target_fps))
        codec_var = tk.StringVar(value=self.codec)
        duration_var = tk.StringVar(
            value=str(getattr(self, "motion_duration_sec", 900) // 60))
        self._field(tab_main, "Корневая папка")
        row = tk.Frame(tab_main, bg=t["BG_CARD"])
        row.pack(fill="x", padx=14, pady=(0, 10))
        tk.Entry(row, textvariable=folder_var, bg=t["BG_INPUT"],
                 fg=t["FG_PRIMARY"], insertbackground=t["FG_PRIMARY"],
                 bd=0, relief="flat", font=("Segoe UI", 10),
                 highlightthickness=1, highlightbackground=t["BORDER"],
                 highlightcolor=t["ACCENT"]
                 ).pack(side="left", fill="x", expand=True, ipady=5)
        RoundedButton(row, "Обзор",
                      command=lambda: self._browse(folder_var),
                      width=80, height=30, bg=t["BG_INPUT"],
                      fg=t["FG_PRIMARY"], hover_bg=t["ACCENT"], theme=t,
                      font=("Segoe UI", 9)
                      ).pack(side="left", padx=(6, 0))
        self._field(tab_main, "Кодек видео")
        ttk.Combobox(tab_main, textvariable=codec_var,
                     values=list(self.codec_map.keys()), state="readonly",
                     style="Dark.TCombobox", font=("Segoe UI", 10)
                     ).pack(fill="x", padx=14, ipady=4)
        self._field(tab_main, "FPS")
        ttk.Combobox(tab_main, textvariable=fps_var,
                     values=["15", "24", "30", "60", "120"],
                     state="readonly", style="Dark.TCombobox",
                     font=("Segoe UI", 10)).pack(fill="x", padx=14, ipady=4)
        self._field(tab_main, "Длительность авто-записи (мин)")
        tk.Entry(tab_main, textvariable=duration_var, bg=t["BG_INPUT"],
                 fg=t["FG_PRIMARY"], insertbackground=t["FG_PRIMARY"],
                 bd=0, relief="flat", font=("Segoe UI", 10),
                 highlightthickness=1, highlightbackground=t["BORDER"],
                 highlightcolor=t["ACCENT"]).pack(fill="x", padx=14, ipady=5)

        tab_det = tk.Frame(notebook, bg=t["BG_CARD"])
        notebook.add(tab_det, text="  Детекция  ")
        self._field(tab_det, "Уровень чувствительности")
        level_var = tk.StringVar(value=self.sensitivity_level)
        level_frame = tk.Frame(tab_det, bg=t["BG_CARD"])
        level_frame.pack(fill="x", padx=14, pady=(0, 6))
        for label, val, desc in (
                ("Низкая", "low", "меньше ложных"),
                ("Средняя", "medium", "баланс"),
                ("Высокая", "high", "ловит мелкое")):
            ttk.Radiobutton(level_frame, text=f"{label} — {desc}",
                            variable=level_var, value=val,
                            style="Dark.TRadiobutton").pack(anchor="w", pady=2)
        sens_var = tk.StringVar(value=str(self.motion_threshold))
        min_area_var = tk.StringVar(value=str(self.motion_min_area))
        confirm_var = tk.StringVar(value=str(self.motion_confirm_frames))
        solidity_var = tk.StringVar(value=str(self.motion_min_solidity))
        photo_cd_var = tk.StringVar(value=str(self.photo_cooldown))
        video_cd_var = tk.StringVar(value=str(self.video_cooldown))
        for label, var in [("Порог пикселей (MOG2)", sens_var),
                           ("Мин. площадь объекта (пиксели)", min_area_var),
                           ("Кадров подтверждения подряд", confirm_var),
                           ("Мин. заполненность контура (0–1)", solidity_var),
                           ("Задержка ФОТО (сек), 0 = каждое", photo_cd_var),
                           ("Задержка ВИДЕО (сек)", video_cd_var)]:
            self._field(tab_det, label)
            tk.Entry(tab_det, textvariable=var, bg=t["BG_INPUT"],
                     fg=t["FG_PRIMARY"], insertbackground=t["FG_PRIMARY"],
                     bd=0, relief="flat", font=("Segoe UI", 10),
                     highlightthickness=1, highlightbackground=t["BORDER"],
                     highlightcolor=t["ACCENT"]
                     ).pack(fill="x", padx=14, ipady=5)

        tab_audio = tk.Frame(notebook, bg=t["BG_CARD"])
        notebook.add(tab_audio, text="  Аудио  ")
        audio_var = tk.BooleanVar(value=self.audio_enabled)
        audio_cb = ttk.Checkbutton(tab_audio,
                                   text="Записывать звук с микрофона",
                                   variable=audio_var,
                                   style="Dark.TCheckbutton")
        audio_cb.pack(anchor="w", padx=14, pady=(14, 6))
        if not AUDIO_AVAILABLE:
            audio_cb.state(["disabled"])
            tk.Label(tab_audio,
                     text="⚠  pyaudio не установлен\n"
                          "pip install pyaudio",
                     bg=t["BG_CARD"], fg=t["WARNING"],
                     font=("Segoe UI", 9), justify="left", anchor="w"
                     ).pack(fill="x", padx=14)
        if not _ffmpeg_exe:
            tk.Label(tab_audio,
                     text="⚠  ffmpeg не найден — аудио не будет "
                          "объединяться с видео",
                     bg=t["BG_CARD"], fg=t["WARNING"],
                     font=("Segoe UI", 9), justify="left", anchor="w"
                     ).pack(fill="x", padx=14)
        alert_sound_var = tk.BooleanVar(value=self.trap_alert_sound_enabled)
        ttk.Checkbutton(tab_audio,
                        text="🔔  Сигнал при срабатывании ловушек",
                        variable=alert_sound_var, style="Dark.TCheckbutton"
                        ).pack(anchor="w", padx=14, pady=(14, 4))
        trap_sound_var = tk.BooleanVar(value=self.trap_sound_enabled)
        ttk.Checkbutton(tab_audio,
                        text="Воспроизводить сигнал через колонки",
                        variable=trap_sound_var, style="Dark.TCheckbutton"
                        ).pack(anchor="w", padx=14, pady=4)
        trap_mode_var = tk.StringVar(value=self.trap_sound_mode)
        mode_frame = tk.Frame(tab_audio, bg=t["BG_CARD"])
        mode_frame.pack(fill="x", padx=14)
        for label, val in [("Только фотоловушка (📷)", "photo"),
                           ("Только видеоловушка (🎥)", "video"),
                           ("Оба срабатывания", "both")]:
            ttk.Radiobutton(mode_frame, text=label,
                            variable=trap_mode_var, value=val,
                            style="Dark.TRadiobutton").pack(anchor="w", pady=2)
        trap_interval_var = tk.StringVar(
            value=str(self.trap_sound_min_interval))
        self._field(tab_audio, "Мин. интервал между сигналами (сек)")
        tk.Entry(tab_audio, textvariable=trap_interval_var,
                 bg=t["BG_INPUT"], fg=t["FG_PRIMARY"],
                 insertbackground=t["FG_PRIMARY"], bd=0, relief="flat",
                 font=("Segoe UI", 10), highlightthickness=1,
                 highlightbackground=t["BORDER"], highlightcolor=t["ACCENT"]
                 ).pack(fill="x", padx=14, ipady=5)

        tab_sys = tk.Frame(notebook, bg=t["BG_CARD"])
        notebook.add(tab_sys, text="  Система  ")
        fs_var = tk.BooleanVar(value=self.start_fullscreen)
        ttk.Checkbutton(tab_sys, text="Запускать в полноэкранном режиме",
                        variable=fs_var, style="Dark.TCheckbutton"
                        ).pack(anchor="w", padx=14, pady=(14, 4))
        traps_var = tk.BooleanVar(value=self.auto_enable_traps)
        ttk.Checkbutton(tab_sys,
                        text="Автоматически включать ловушки при старте",
                        variable=traps_var, style="Dark.TCheckbutton"
                        ).pack(anchor="w", padx=14, pady=4)
        grid_var = tk.BooleanVar(value=self.grid_view)
        ttk.Checkbutton(tab_sys, text="Показывать все камеры в сетке",
                        variable=grid_var, style="Dark.TCheckbutton"
                        ).pack(anchor="w", padx=14, pady=4)
        tk.Label(tab_sys,
                 text=("Клавиши: A архив • Space старт/pause\n"
                       "←/→ −5/+5с • PgUp/PgDn −30/+30с • Home в начало\n"
                       "K сохранить кадр • ,/. скорость\n"
                       "Enter полный экран • F11 полный • Esc выход"),
                 bg=t["BG_CARD"], fg=t["FG_MUTED"],
                 font=("Segoe UI", 9), justify="left", anchor="w"
                 ).pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(tab_sys, text="Автозапуск с Windows",
                 bg=t["BG_CARD"], fg=t["FG_PRIMARY"],
                 font=("Segoe UI", 11, "bold"), anchor="w"
                 ).pack(fill="x", padx=14, pady=(8, 6))
        autostart_var = tk.BooleanVar(value=self.autostart_enabled)
        ttk.Checkbutton(tab_sys, text="Запускать при входе в Windows",
                        variable=autostart_var, style="Dark.TCheckbutton"
                        ).pack(anchor="w", padx=14, pady=4)
        as_btn_frame = tk.Frame(tab_sys, bg=t["BG_CARD"])
        as_btn_frame.pack(fill="x", padx=14, pady=(8, 14))

        def apply_autostart_now(enable):
            if enable:
                ok, msg = enable_autostart()
            else:
                ok, msg = disable_autostart()
            if ok:
                self.autostart_enabled = enable
                self.save_config()
                autostart_var.set(enable)
                Toast.show(win, msg, kind="success", theme=self.theme)
            else:
                Toast.show(win, msg, kind="error", theme=self.theme)
                autostart_var.set(not enable)

        RoundedButton(as_btn_frame, "Включить сейчас",
                      command=lambda: apply_autostart_now(True),
                      width=140, height=32, bg=t["SUCCESS_DARK"],
                      fg="#ffffff", hover_bg=t["SUCCESS"], theme=t,
                      font=("Segoe UI", 9, "bold"), icon="✓"
                      ).pack(side="left", padx=(0, 6))
        RoundedButton(as_btn_frame, "Отключить сейчас",
                      command=lambda: apply_autostart_now(False),
                      width=150, height=32, bg=t["BG_INPUT"],
                      fg=t["FG_PRIMARY"], hover_bg=t["DANGER"], theme=t,
                      font=("Segoe UI", 9), icon="✕"
                      ).pack(side="left", padx=6)

        tab_about = tk.Frame(notebook, bg=t["BG_CARD"])
        notebook.add(tab_about, text="  О программе  ")
        tk.Label(tab_about, text=f"{APP_NAME}\nВерсия {VERSION}",
                 bg=t["BG_CARD"], fg=t["FG_PRIMARY"],
                 font=("Segoe UI", 13, "bold"), justify="left"
                 ).pack(anchor="w", padx=14, pady=(14, 6))
        backend_txt = (ARCHIVE_PLAYER_BACKEND
                       if ARCHIVE_PLAYER_AVAILABLE else "недоступен")
        audio_txt = "OK" if AUDIO_AVAILABLE else "не установлен"
        ffmpeg_txt = "OK" if _ffmpeg_exe else "не найден"
        tk.Label(tab_about,
                 text=(f"Backend архива: {backend_txt}\n"
                       f"Аудио (pyaudio): {audio_txt}\n"
                       f"ffmpeg: {ffmpeg_txt}\n"
                       f"OpenCV threads: {cv2.getNumThreads()}\n"
                       f"CPU cores: {_CPU_COUNT}\n\n"
                       "Структура папок:\n"
                       "  <корень>/Camera_<N>/Photos/ — фотографии\n"
                       "  <корень>/Camera_<N>/Videos/ — видеозаписи\n\n"
                       "Умные имена файлов:\n"
                       "  «Видео №1 — 03.10.2026 23.45.16.avi»\n"
                       "  «Фото №1 — 03.10.2026 23.45.16.123.jpg»\n"
                       "  «Запись №1 — 03.10.2026 23.45.16.avi»\n\n"
                       "Наложение штампа CAM + дата + время:\n"
                       "  • на превью и видеофайлах\n"
                       "  • на фотоловушке и тестовых снимках\n\n"
                       "Плеер архива: перемотка, скорость, покадровый шаг,\n"
                       "сохранение кадров."),
                 bg=t["BG_CARD"], fg=t["FG_SECONDARY"],
                 font=("Consolas", 9), justify="left", anchor="w"
                 ).pack(anchor="w", padx=14, pady=6)

        buttons = tk.Frame(win, bg=t["BG_MAIN"])
        buttons.pack(fill="x", padx=10, pady=(0, 10))

        def apply():
            new_folder = folder_var.get().strip()
            if new_folder and new_folder != self.output_path:
                try:
                    os.makedirs(new_folder, exist_ok=True)
                    test = os.path.join(new_folder, ".write_test")
                    with open(test, "wb") as f:
                        f.write(b"ok")
                    os.remove(test)
                    self.output_path = new_folder
                    self.photos_folder = os.path.join(self.output_path,
                                                      "Photos")
                    os.makedirs(self.photos_folder, exist_ok=True)
                    for ch in self.channels.values():
                        ch._refresh_folders()
                        ch.invalidate_file_count_cache()
                except Exception as e:
                    messagebox.showerror("Ошибка",
                                         f"Не создать папку:\n{e}")
            try:
                fps = float(fps_var.get())
                if fps > 0:
                    self.target_fps = fps
            except ValueError:
                pass
            try:
                mins = max(1, int(duration_var.get()))
                self.motion_duration_sec = mins * 60
                self.auto_stop_ms = self.motion_duration_sec * 1000
            except ValueError:
                pass
            old_level = self.sensitivity_level
            self.sensitivity_level = level_var.get()
            if old_level != self.sensitivity_level:
                self._apply_sensitivity_preset()
            else:
                for var, attr, caster in (
                        (sens_var, "motion_threshold", int),
                        (min_area_var, "motion_min_area", int),
                        (confirm_var, "motion_confirm_frames", int)):
                    try:
                        setattr(self, attr, max(1, caster(var.get())))
                    except ValueError:
                        pass
                try:
                    self.motion_min_solidity = max(
                        0.05, min(1.0, float(solidity_var.get())))
                except ValueError:
                    pass
                try:
                    self.photo_cooldown = max(0.0, float(photo_cd_var.get()))
                except ValueError:
                    pass
                try:
                    self.video_cooldown = max(0.1, float(video_cd_var.get()))
                except ValueError:
                    pass
            self.audio_enabled = audio_var.get() and AUDIO_AVAILABLE
            self.codec = codec_var.get()
            self.extension = self.codec_map[self.codec][1]
            self.start_fullscreen = fs_var.get()
            self.auto_enable_traps = traps_var.get()
            self.trap_alert_sound_enabled = alert_sound_var.get()
            self.trap_sound_enabled = trap_sound_var.get()
            self.trap_sound_mode = trap_mode_var.get()
            try:
                self.trap_sound_min_interval = max(
                    0.0, float(trap_interval_var.get()))
            except ValueError:
                pass
            new_grid = grid_var.get()
            if new_grid != self.grid_view:
                self.grid_view = new_grid
                self.view_btn.set_text("Сетка" if self.grid_view else "Одна",
                                       "")
                if self.grid_view:
                    self.view_btn.set_colors(
                        bg=self.theme["ACCENT"],
                        hover=self.theme["ACCENT_HOVER"], fg="#ffffff")
                else:
                    self.view_btn.set_colors(
                        bg=self.theme["BG_INPUT"],
                        hover=self.theme["ACCENT"],
                        fg=self.theme["FG_PRIMARY"])
                self._rebuild_grid()
            new_autostart = autostart_var.get()
            if new_autostart != self.autostart_enabled:
                if new_autostart:
                    ok, msg = enable_autostart()
                else:
                    ok, msg = disable_autostart()
                if ok:
                    self.autostart_enabled = new_autostart
                else:
                    Toast.show(win, msg, kind="error", theme=self.theme)
            self.save_config()
            win.destroy()
            Toast.show(self.root, "Настройки применены",
                       kind="success", theme=self.theme)

        RoundedButton(buttons, "Применить", command=apply,
                      width=130, height=38, bg=t["SUCCESS_DARK"],
                      fg="#ffffff", hover_bg=t["SUCCESS"], theme=t,
                      font=("Segoe UI", 10, "bold"), icon="✓"
                      ).pack(side="left", padx=(0, 6))
        RoundedButton(buttons, "Отмена", command=win.destroy,
                      width=110, height=38, bg=t["BG_INPUT"],
                      fg=t["FG_PRIMARY"], hover_bg=t["BG_HOVER"], theme=t,
                      font=("Segoe UI", 10)
                      ).pack(side="left")
        RoundedButton(buttons, "Сбросить",
                      command=lambda: self._reset_settings(win),
                      width=110, height=38, bg=t["BG_INPUT"],
                      fg=t["FG_PRIMARY"], hover_bg=t["DANGER"], theme=t,
                      font=("Segoe UI", 10)
                      ).pack(side="right")

    def _field(self, parent, text):
        tk.Label(parent, text=text, bg=self.theme["BG_CARD"],
                 fg=self.theme["FG_SECONDARY"],
                 font=("Segoe UI", 9), anchor="w"
                 ).pack(fill="x", padx=14, pady=(10, 3))

    def _browse(self, var):
        folder = filedialog.askdirectory(title="Выберите папку",
                                         initialdir=var.get())
        if folder:
            var.set(folder)

    def _apply_sensitivity_preset(self):
        presets = {
            "low": {"threshold": 200, "min_area": 3000, "confirm": 3,
                    "photo_cd": 2.0, "video_cd": 2.0, "solidity": 0.45},
            "medium": {"threshold": 100, "min_area": 1500, "confirm": 1,
                       "photo_cd": 0.0, "video_cd": 1.5, "solidity": 0.35},
            "high": {"threshold": 50, "min_area": 800, "confirm": 1,
                     "photo_cd": 0.0, "video_cd": 1.0, "solidity": 0.25},
        }
        p = presets.get(self.sensitivity_level, presets["medium"])
        self.motion_threshold = p["threshold"]
        self.motion_min_area = p["min_area"]
        self.motion_confirm_frames = p["confirm"]
        self.photo_cooldown = p["photo_cd"]
        self.video_cooldown = p["video_cd"]
        self.motion_min_solidity = p["solidity"]

    def _reset_settings(self, win):
        if not messagebox.askyesno(
                "Сброс", "Сбросить настройки к значениям по умолчанию?"):
            return
        if os.path.exists(CONFIG_FILE):
            try:
                os.remove(CONFIG_FILE)
            except Exception:
                pass
        self.output_path = os.path.join(os.path.expanduser("~"),
                                        "Videos", "Fotolovushka")
        self.photos_folder = os.path.join(self.output_path, "Photos")
        os.makedirs(self.photos_folder, exist_ok=True)
        for ch in self.channels.values():
            ch._refresh_folders()
            ch.invalidate_file_count_cache()
        self.target_fps = 30
        self.motion_threshold = 100
        self.photo_cooldown = 0.0
        self.video_cooldown = 1.5
        self.motion_min_area = 1500
        self.motion_max_area_ratio = 0.6
        self.motion_confirm_frames = 1
        self.motion_min_solidity = 0.35
        self.sensitivity_level = "medium"
        self.motion_ignore_zones = []
        self.audio_enabled = AUDIO_AVAILABLE
        self.codec = "XVID"
        self.extension = ".avi"
        self.motion_duration_sec = 15 * 60
        self.auto_stop_ms = self.motion_duration_sec * 1000
        self.start_fullscreen = True
        self.auto_enable_traps = True
        self.trap_alert_sound_enabled = True
        self.trap_sound_enabled = True
        self.trap_sound_mode = "both"
        self.trap_sound_min_interval = 1.5
        self.grid_view = True
        self.save_config()
        win.destroy()
        Toast.show(self.root, "Настройки сброшены",
                   kind="info", theme=self.theme)

    def on_close(self):
        self._ui_queue_running = False
        if IS_WINDOWS:
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            except Exception:
                pass
        if getattr(self, "_screen_is_off", False):
            try:
                self._wake_screen()
            except Exception:
                pass
        try:
            self.archive_stop()
        except Exception:
            pass
        try:
            self.archive_preview_playing = False
            if self.archive_preview_window is not None:
                self.archive_preview_window.destroy()
        except Exception:
            pass
        try:
            if self._config_save_timer is not None:
                try:
                    self.root.after_cancel(self._config_save_timer)
                except Exception:
                    pass
                self._config_save_timer = None
            self._save_config_now()
        except Exception:
            pass
        try:
            futs = []
            for ch in list(self.channels.values()):
                futs.append(self._control_pool.submit(ch.close))
            for f in futs:
                try:
                    f.result(timeout=2.0)
                except Exception:
                    pass
        except Exception:
            pass
        self.channels.clear()
        for pool in (self._thumb_pool, self._scan_pool,
                     self._open_pool, self._control_pool):
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        try:
            if self._fullscreen_active:
                self.root.attributes("-fullscreen", False)
        except Exception:
            pass
        for p in (SIGNAL_PHOTO_WAV_PATH, SIGNAL_VIDEO_WAV_PATH):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        time.sleep(0.2)
        try:
            self.root.destroy()
        except Exception:
            pass


# ============================================================
#                       ЗАПУСК
# ============================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = VideoRecorderApp(root)
    root.mainloop()