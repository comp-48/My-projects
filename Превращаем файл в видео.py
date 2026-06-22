# youtube_storage_fixed.py
import cv2
import numpy as np
import os
import math
import subprocess
import tempfile
import shutil
import sys
import re
import hashlib
import threading
import queue
import time
from collections import Counter

# ========== GUI IMPORTS ==========
try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, filedialog, messagebox, simpledialog
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False
    print("⚠️ Tkinter не найден. GUI недоступен.")


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_password_gui(prompt="Введите ключ шифрования (или оставьте пустым для отключения):",
                     title="Ключ шифрования"):
    """
    Запрашивает пароль через графический диалог с маскировкой.
    Если tkinter недоступен, использует консольный ввод.
    Возвращает строку с паролем или None (если пользователь отменил или ввел пустую строку).
    """
    password = None
    if TKINTER_AVAILABLE:
        try:
            root = tk.Tk()
            root.withdraw()  # Скрыть главное окно
            password = simpledialog.askstring(title, prompt, show='*', parent=root)
            root.destroy()
        except Exception as e:
            print(f"⚠️ Ошибка графического диалога: {e}. Использую консольный ввод.")
            password = input(prompt + " (введите пароль): ")
    else:
        print("⚠️ Tkinter не найден. Использую консольный ввод.")
        password = input(prompt + " (введите пароль): ")

    if password is None or password.strip() == "":
        return None
    return password.strip()


# ========== КЛАССЫ КОДЕРА / ДЕКОДЕРА (с поддержкой колбэков) ==========
class YouTubeEncoder:
    def __init__(self, key=None, log_callback=None, progress_callback=None):
        self.width = 1920
        self.height = 1080
        self.fps = 6

        self.block_height = 16
        self.block_width = 24
        self.spacing = 4

        self.key = key
        self.use_encryption = key is not None

        self.colors = {
            '0000': (255, 0, 0),
            '0001': (0, 255, 0),
            '0010': (0, 0, 255),
            '0011': (255, 255, 0),
            '0100': (255, 0, 255),
            '0101': (0, 255, 255),
            '0110': (255, 128, 0),
            '0111': (128, 0, 255),
            '1000': (0, 128, 128),
            '1001': (128, 128, 0),
            '1010': (128, 0, 128),
            '1011': (0, 128, 0),
            '1100': (128, 0, 0),
            '1101': (0, 0, 128),
            '1110': (192, 192, 192),
            '1111': (255, 255, 255)
        }

        self.marker_size = 80
        self.blocks_x = (self.width - 2*self.marker_size) // (self.block_width + self.spacing)
        self.blocks_y = (self.height - 2*self.marker_size) // (self.block_height + self.spacing)
        self.blocks_per_region = self.blocks_x * self.blocks_y
        self.blocks_per_frame = self.blocks_per_region * 3

        self.eof_marker = "█" * 64
        self.eof_bytes = self.eof_marker.encode('utf-8')

        # Колбэки
        self.log_callback = log_callback
        self.progress_callback = progress_callback

        self._log("="*60)
        self._log("🎬 КОДИРОВЩИК YouTube (6 FPS)")
        self._log("="*60)
        self._log(f"📊 Сетка: {self.blocks_x} x {self.blocks_y} блоков на регион")
        self._log(f"🎞️  FPS: {self.fps}")
        self._log(f"🔐 Шифрование: {'ВКЛ' if self.use_encryption else 'ВЫКЛ'}")

    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
        else:
            print(msg)

    def _progress(self, percent):
        if self.progress_callback:
            self.progress_callback(percent)

    def _encrypt_data(self, data):
        if not self.use_encryption:
            return data
        key_bytes = self.key.encode()
        result = bytearray()
        for i, byte in enumerate(data):
            key_byte = key_bytes[i % len(key_bytes)]
            result.append(byte ^ key_byte)
        return result

    def _draw_markers(self, frame):
        cv2.rectangle(frame, (0, 0), (self.marker_size, self.marker_size), (255, 255, 255), -1)
        cv2.rectangle(frame, (self.width-self.marker_size, 0), (self.width, self.marker_size), (255, 255, 255), -1)
        cv2.rectangle(frame, (0, self.height-self.marker_size), (self.marker_size, self.height), (255, 255, 255), -1)
        cv2.rectangle(frame, (self.width-self.marker_size, self.height-self.marker_size), (self.width, self.height), (255, 255, 255), -1)
        cv2.rectangle(frame, (0, 0), (self.marker_size, self.marker_size), (0, 0, 0), 2)
        cv2.rectangle(frame, (self.width-self.marker_size, 0), (self.width, self.marker_size), (0, 0, 0), 2)
        cv2.rectangle(frame, (0, self.height-self.marker_size), (self.marker_size, self.height), (0, 0, 0), 2)
        cv2.rectangle(frame, (self.width-self.marker_size, self.height-self.marker_size), (self.width, self.height), (0, 0, 0), 2)
        return frame

    def _draw_block(self, frame, x, y, color):
        x1 = self.marker_size + x * (self.block_width + self.spacing)
        y1 = self.marker_size + y * (self.block_height + self.spacing)
        x2 = x1 + self.block_width
        y2 = y1 + self.block_height
        if x2 > self.width - self.marker_size or y2 > self.height - self.marker_size:
            return False
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, -1)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 0), 1)
        return True

    def _bits_to_color(self, bits):
        while len(bits) < 4:
            bits = '0' + bits
        return self.colors.get(bits, (255, 0, 0))

    def _data_to_blocks(self, data):
        all_bits = []
        for byte in data:
            for i in range(7, -1, -1):
                all_bits.append(str((byte >> i) & 1))
        while len(all_bits) % 4 != 0:
            all_bits.append('0')
        blocks = [''.join(all_bits[i:i+4]) for i in range(0, len(all_bits), 4)]
        return blocks

    def encode(self, input_file, output_file):
        self._log("\n📤 КОДИРОВАНИЕ ФАЙЛА")
        self._log("-" * 40)

        with open(input_file, 'rb') as f:
            data = f.read()

        self._log(f"📄 Файл: {input_file}")
        self._log(f"📦 Размер: {len(data)} байт")

        if self.use_encryption:
            encrypted_data = self._encrypt_data(data)
            self._log(f"🔐 Данные зашифрованы")
        else:
            encrypted_data = data

        header = f"FILE:{os.path.basename(input_file)}:SIZE:{len(data)}|"
        header_bytes = header.encode('latin-1')
        self._log(f"📋 Заголовок: {header}")

        header_blocks = self._data_to_blocks(header_bytes)
        data_blocks = self._data_to_blocks(encrypted_data)
        eof_blocks = self._data_to_blocks(self.eof_bytes)
        all_blocks = header_blocks + data_blocks + eof_blocks

        self._log(f"🎨 Всего блоков: {len(all_blocks)}")
        self._log(f"🏁 Маркер конца: {len(eof_blocks)} блоков")

        frames_needed = math.ceil(len(all_blocks) / self.blocks_per_region) + 5
        self._log(f"🎬 Требуется кадров: {frames_needed}")
        self._log(f"⏱️  Длительность видео: {frames_needed/self.fps:.1f} сек")

        temp_dir = tempfile.mkdtemp()
        self._log(f"📁 Временная папка: {temp_dir}")

        total_frames = frames_needed
        for frame_num in range(total_frames - 5):
            self._log(f"\n🖼️  Кадр {frame_num + 1}/{total_frames}")
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            frame = self._draw_markers(frame)

            start_idx = frame_num * self.blocks_per_region
            end_idx = min(start_idx + self.blocks_per_region, len(all_blocks))
            frame_blocks = all_blocks[start_idx:end_idx]

            for idx, bits in enumerate(frame_blocks):
                y = idx // self.blocks_x
                x = idx % self.blocks_x
                if y < self.blocks_y:
                    color = self._bits_to_color(bits)
                    self._draw_block(frame, x, y, color)

            for idx, bits in enumerate(frame_blocks):
                y = idx // self.blocks_x
                x = idx % self.blocks_x + self.blocks_x
                if x < self.blocks_x * 2 and y < self.blocks_y:
                    color = self._bits_to_color(bits)
                    self._draw_block(frame, x, y, color)

            for idx, bits in enumerate(frame_blocks):
                y = idx // self.blocks_x + self.blocks_y
                x = idx % self.blocks_x
                if x < self.blocks_x and y < self.blocks_y * 2:
                    color = self._bits_to_color(bits)
                    self._draw_block(frame, x, y, color)

            frame_file = os.path.join(temp_dir, f"frame_{frame_num:05d}.png")
            cv2.imwrite(frame_file, frame)

            # Прогресс
            percent = (frame_num + 1) / total_frames * 100
            self._progress(percent)

        self._log("\n🛡️  Создание защитных кадров...")
        for i in range(5):
            frame_num = total_frames - 5 + i
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            frame = self._draw_markers(frame)
            for y in range(self.blocks_y * 2):
                for x in range(self.blocks_x * 2):
                    self._draw_block(frame, x, y, (255, 0, 0))
            frame_file = os.path.join(temp_dir, f"frame_{frame_num:05d}.png")
            cv2.imwrite(frame_file, frame)
            self._log(f"  🟦 Защитный кадр {i+1}/5")
            self._progress((frame_num + 1) / total_frames * 100)

        self._log("\n🎞️  Конвертация в MP4...")
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            cmd = [
                'ffmpeg',
                '-framerate', str(self.fps),
                '-i', os.path.join(temp_dir, 'frame_%05d.png'),
                '-c:v', 'libx264',
                '-preset', 'slow',
                '-crf', '23',
                '-pix_fmt', 'yuv420p',
                '-an',
                '-movflags', '+faststart',
                '-y',
                output_file
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            self._log("✅ FFmpeg конвертация успешна")
        except Exception as e:
            self._log(f"⚠️ FFmpeg не доступен, использую OpenCV...")
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(output_file, fourcc, self.fps, (self.width, self.height))
            for frame_num in range(total_frames):
                frame_file = os.path.join(temp_dir, f"frame_{frame_num:05d}.png")
                frame = cv2.imread(frame_file)
                if frame is not None:
                    out.write(frame)
            out.release()

        shutil.rmtree(temp_dir)
        self._log("🧹 Временные файлы удалены")

        if os.path.exists(output_file):
            size = os.path.getsize(output_file)
            self._log(f"\n✅ Видео сохранено: {output_file}")
            self._log(f"📊 Размер: {size} байт ({size/1024/1024:.2f} MB)")
            self._log(f"🎬 Кадров: {total_frames}")
            self._log(f"⏱️  Длительность: {total_frames/self.fps:.1f} сек")
            self._progress(100)
            return True
        return False


class YouTubeDecoder:
    def __init__(self, key=None, log_callback=None, progress_callback=None):
        self.width = 1920
        self.height = 1080
        self.block_height = 16
        self.block_width = 24
        self.spacing = 4
        self.marker_size = 80

        self.key = key

        self.colors = {
            '0000': (255, 0, 0),
            '0001': (0, 255, 0),
            '0010': (0, 0, 255),
            '0011': (255, 255, 0),
            '0100': (255, 0, 255),
            '0101': (0, 255, 255),
            '0110': (255, 128, 0),
            '0111': (128, 0, 255),
            '1000': (0, 128, 128),
            '1001': (128, 128, 0),
            '1010': (128, 0, 128),
            '1011': (0, 128, 0),
            '1100': (128, 0, 0),
            '1101': (0, 0, 128),
            '1110': (192, 192, 192),
            '1111': (255, 255, 255)
        }

        self.color_values = np.array(list(self.colors.values()), dtype=np.int32)
        self.color_keys = list(self.colors.keys())
        self.color_cache = {}
        self.cache_hits = 0
        self.cache_misses = 0

        self.blocks_x = (self.width - 2*self.marker_size) // (self.block_width + self.spacing)
        self.blocks_y = (self.height - 2*self.marker_size) // (self.block_height + self.spacing)
        self.blocks_per_region = self.blocks_x * self.blocks_y

        self._precompute_coordinates()

        # Колбэки
        self.log_callback = log_callback
        self.progress_callback = progress_callback

        self._log("="*60)
        self._log("🎬 ДЕКОДЕР YouTube")
        self._log("="*60)
        self._log(f"📊 Сетка: {self.blocks_x} x {self.blocks_y} блоков")
        self._log(f"🔐 Ключ: {'ЕСТЬ' if self.key else 'НЕТ'}")

    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
        else:
            print(msg)

    def _progress(self, percent):
        if self.progress_callback:
            self.progress_callback(percent)

    def _precompute_coordinates(self):
        self.block_coords = []
        for idx in range(self.blocks_per_region):
            y = idx // self.blocks_x
            x = idx % self.blocks_x
            if y < self.blocks_y:
                cx = self.marker_size + x * (self.block_width + self.spacing) + self.block_width // 2
                cy = self.marker_size + y * (self.block_height + self.spacing) + self.block_height // 2
                self.block_coords.append((cx, cy))

    def _decrypt_data(self, data):
        if not self.key:
            return data
        key_bytes = self.key.encode()
        result = bytearray()
        for i, byte in enumerate(data):
            key_byte = key_bytes[i % len(key_bytes)]
            result.append(byte ^ key_byte)
        return result

    def _color_to_bits_fast(self, color):
        color_key = (color[0], color[1], color[2])
        if color_key in self.color_cache:
            self.cache_hits += 1
            return self.color_cache[color_key]
        self.cache_misses += 1
        if color[0] > 200 and color[1] < 50 and color[2] < 50:
            self.color_cache[color_key] = '0000'
            return '0000'
        color_arr = np.array([color[0], color[1], color[2]], dtype=np.int32)
        distances = np.sum((self.color_values - color_arr) ** 2, axis=1)
        best_idx = np.argmin(distances)
        result = self.color_keys[best_idx]
        self.color_cache[color_key] = result
        return result

    def decode_frame_fast(self, frame):
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_NEAREST)
        blocks = []
        h, w = frame.shape[:2]
        for cx, cy in self.block_coords:
            if cx < w and cy < h:
                color = frame[cy, cx]
                bits = self._color_to_bits_fast(color)
                blocks.append(bits)
            else:
                blocks.append('0000')
        return blocks

    def _blocks_to_bytes(self, blocks):
        all_bits = ''.join(blocks)
        bytes_data = bytearray()
        for i in range(0, len(all_bits) - 7, 8):
            byte_str = all_bits[i:i+8]
            if len(byte_str) == 8:
                try:
                    byte = int(byte_str, 2)
                    bytes_data.append(byte)
                except:
                    bytes_data.append(0)
        return bytes_data

    def _find_eof_marker(self, data):
        eof_bytes = b'\xe2\x96\x88' * 64
        for i in range(len(data) - len(eof_bytes)):
            if data[i:i+len(eof_bytes)] == eof_bytes:
                return i
        return -1

    def decode(self, video_file, output_dir='.'):
        self._log("\n📥 ДЕКОДИРОВАНИЕ ВИДЕО")
        self._log("-" * 40)

        if not os.path.exists(video_file):
            self._log(f"❌ Файл не найден: {video_file}")
            return False

        cap = cv2.VideoCapture(video_file)
        if not cap.isOpened():
            self._log("❌ Не удалось открыть видео")
            return False

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        self._log(f"📹 Всего кадров: {total_frames}")
        self._log(f"📹 FPS: {fps}")
        self._log(f"📹 Разрешение: {width}x{height}")

        self.cache_hits = 0
        self.cache_misses = 0
        start_time = cv2.getTickCount()

        all_blocks = []
        frames_processed = 0

        for frame_num in range(total_frames):
            ret, frame = cap.read()
            if not ret:
                break
            frames_processed += 1

            if frame_num % 100 == 0:
                elapsed = (cv2.getTickCount() - start_time) / cv2.getTickFrequency()
                speed = frames_processed / elapsed if elapsed > 0 else 0
                cache_ratio = (self.cache_hits / (self.cache_hits + self.cache_misses) * 100) if (self.cache_hits + self.cache_misses) > 0 else 0
                self._log(f"  Прогресс: {frame_num}/{total_frames} | "
                          f"Скорость: {speed:.1f} кадр/сек | "
                          f"Кэш: {cache_ratio:.1f}%")

            frame_blocks = self.decode_frame_fast(frame)
            all_blocks.extend(frame_blocks)

            # Прогресс
            percent = (frame_num + 1) / total_frames * 100
            self._progress(percent)

        cap.release()

        elapsed = (cv2.getTickCount() - start_time) / cv2.getTickFrequency()
        self._log(f"\n📊 Статистика: {len(all_blocks)} блоков за {elapsed:.1f} сек")
        self._log(f"  🎯 Кэш: попаданий {self.cache_hits}, промахов {self.cache_misses}")
        self._log(f"  🔄 Кадров обработано: {frames_processed}")

        bytes_data = self._blocks_to_bytes(all_blocks)
        self._log(f"📦 Получено байт: {len(bytes_data)}")

        eof_pos = self._find_eof_marker(bytes_data)
        if eof_pos > 0:
            bytes_data = bytes_data[:eof_pos]
            self._log(f"✅ Найден маркер конца на позиции {eof_pos}")
            self._log(f"📦 Байт после обрезки: {len(bytes_data)}")
        else:
            self._log("⚠️ Маркер конца не найден")

        data_str = bytes_data[:1000].decode('latin-1', errors='ignore')
        pattern = r'FILE:([^:]+):SIZE:(\d+)\|'
        match = re.search(pattern, data_str)

        if match:
            filename = match.group(1)
            filesize = int(match.group(2))
            self._log(f"\n✅ Найден заголовок: {filename}, размер: {filesize} байт")

            header_str = match.group(0)
            header_bytes = header_str.encode('latin-1')
            header_pos = bytes_data.find(header_bytes)

            if header_pos >= 0:
                encrypted_data = bytes_data[header_pos + len(header_bytes):header_pos + len(header_bytes) + filesize]
                if self.key:
                    file_data = self._decrypt_data(encrypted_data)
                    self._log(f"🔓 Данные расшифрованы")
                else:
                    file_data = encrypted_data
                    self._log(f"⚠️ Данные без расшифровки")

                output_path = os.path.join(output_dir, filename)
                counter = 1
                base, ext = os.path.splitext(filename)
                while os.path.exists(output_path):
                    output_path = os.path.join(output_dir, f"{base}_{counter}{ext}")
                    counter += 1

                with open(output_path, 'wb') as f:
                    f.write(file_data)

                self._log(f"\n✅ Файл восстановлен: {output_path}")
                self._log(f"📏 Размер: {len(file_data)} байт")
                if len(file_data) == filesize:
                    self._log("✅ Размер совпадает с оригиналом")
                else:
                    self._log(f"⚠️ Размер не совпадает: {len(file_data)} != {filesize}")
                self._progress(100)
                return True
        else:
            self._log("❌ Заголовок не найден")

        output_path = os.path.join(output_dir, "decoded_data.bin")
        with open(output_path, 'wb') as f:
            f.write(bytes_data)
        self._log(f"\n💾 Данные сохранены: {output_path}")
        self._progress(100)
        return False


# ========== ГРАФИЧЕСКИЙ ИНТЕРФЕЙС ==========
class GUIApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube File Storage – GUI")
        self.geometry("850x650")
        self.minsize(700, 500)

        self.mode = tk.StringVar(value="encode")
        self.input_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.password = tk.StringVar()

        self.progress_var = tk.DoubleVar()
        self.progress_var.set(0)

        self.running = False
        self.thread = None
        self.stop_flag = False

        self._create_widgets()
        self._update_mode()

    def _create_widgets(self):
        # Основной контейнер с отступами
        main = ttk.Frame(self, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # === Режим ===
        mode_frame = ttk.LabelFrame(main, text="Режим", padding=5)
        mode_frame.pack(fill=tk.X, pady=5)

        ttk.Radiobutton(mode_frame, text="Кодировать (файл → видео)", variable=self.mode,
                        value="encode", command=self._update_mode).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(mode_frame, text="Декодировать (видео → файл)", variable=self.mode,
                        value="decode", command=self._update_mode).pack(side=tk.LEFT, padx=5)

        # === Входной файл ===
        in_frame = ttk.Frame(main)
        in_frame.pack(fill=tk.X, pady=5)
        ttk.Label(in_frame, text="Входной файл:").pack(side=tk.LEFT, padx=5)
        self.in_entry = ttk.Entry(in_frame, textvariable=self.input_path, width=50)
        self.in_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(in_frame, text="Обзор...", command=self._browse_input).pack(side=tk.RIGHT, padx=5)

        # === Выходной файл/папка ===
        out_frame = ttk.Frame(main)
        out_frame.pack(fill=tk.X, pady=5)
        ttk.Label(out_frame, text="Выходной:").pack(side=tk.LEFT, padx=5)
        self.out_entry = ttk.Entry(out_frame, textvariable=self.output_path, width=50)
        self.out_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(out_frame, text="Обзор...", command=self._browse_output).pack(side=tk.RIGHT, padx=5)

        # === Пароль ===
        pass_frame = ttk.Frame(main)
        pass_frame.pack(fill=tk.X, pady=5)
        ttk.Label(pass_frame, text="Ключ шифрования:").pack(side=tk.LEFT, padx=5)
        self.pass_entry = ttk.Entry(pass_frame, textvariable=self.password, show="*", width=40)
        self.pass_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(pass_frame, text="Очистить", command=lambda: self.password.set("")).pack(side=tk.RIGHT, padx=5)

        # === Кнопки управления ===
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=5)
        self.start_btn = ttk.Button(btn_frame, text="▶ Запустить", command=self._start_task)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(btn_frame, text="⏹ Остановить", command=self._stop_task, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Очистить лог", command=self._clear_log).pack(side=tk.RIGHT, padx=5)

        # === Прогресс ===
        prog_frame = ttk.Frame(main)
        prog_frame.pack(fill=tk.X, pady=5)
        self.progress_bar = ttk.Progressbar(prog_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=5)

        # === Лог ===
        log_frame = ttk.LabelFrame(main, text="Лог", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=15, state='normal')
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.config(font=("Consolas", 9))

    def _update_mode(self):
        mode = self.mode.get()
        if mode == "encode":
            self.out_entry.config(state='normal')
            self.output_path.set("output.mp4")
        else:
            self.out_entry.config(state='normal')
            self.output_path.set(".")

    def _browse_input(self):
        mode = self.mode.get()
        if mode == "encode":
            filetypes = [("Все файлы", "*.*")]
            f = filedialog.askopenfilename(title="Выберите файл для кодирования", filetypes=filetypes)
        else:
            filetypes = [("Видео файлы", "*.mp4 *.avi *.mov *.mkv"), ("Все файлы", "*.*")]
            f = filedialog.askopenfilename(title="Выберите видео для декодирования", filetypes=filetypes)
        if f:
            self.input_path.set(f)
            # Автоподстановка имени выходного файла
            if mode == "encode":
                base = os.path.splitext(os.path.basename(f))[0]
                self.output_path.set(base + "_encoded.mp4")
            else:
                self.output_path.set(os.path.dirname(f))

    def _browse_output(self):
        mode = self.mode.get()
        if mode == "encode":
            f = filedialog.asksaveasfilename(title="Сохранить видео как", defaultextension=".mp4",
                                             filetypes=[("MP4 файлы", "*.mp4"), ("Все файлы", "*.*")])
            if f:
                self.output_path.set(f)
        else:
            d = filedialog.askdirectory(title="Выберите папку для сохранения файла")
            if d:
                self.output_path.set(d)

    def _log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def _clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def _set_progress(self, value):
        self.progress_var.set(value)
        self.update_idletasks()

    def _start_task(self):
        if self.running:
            return

        mode = self.mode.get()
        input_file = self.input_path.get().strip()
        output = self.output_path.get().strip()
        key = self.password.get().strip()
        if not key:
            key = None

        if not input_file or not output:
            messagebox.showerror("Ошибка", "Заполните все поля.")
            return

        if mode == "encode" and not os.path.isfile(input_file):
            messagebox.showerror("Ошибка", "Входной файл не существует.")
            return
        if mode == "decode" and not os.path.isfile(input_file):
            messagebox.showerror("Ошибка", "Входное видео не существует.")
            return

        # Проверка существования выходного файла (для encode)
        if mode == "encode" and os.path.exists(output):
            if not messagebox.askyesno("Файл существует", f"Файл {output} уже существует. Перезаписать?"):
                return

        self.running = True
        self.stop_flag = False
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self._log("=" * 60)
        self._log(f"Запуск {mode} с файлом: {input_file}")
        if key:
            self._log("🔐 Шифрование включено")
        else:
            self._log("🔓 Шифрование отключено")

        # Запуск в потоке
        self.thread = threading.Thread(target=self._run_task, args=(mode, input_file, output, key), daemon=True)
        self.thread.start()

    def _run_task(self, mode, input_file, output, key):
        try:
            if mode == "encode":
                encoder = YouTubeEncoder(key=key,
                                         log_callback=self._log_gui,
                                         progress_callback=self._progress_gui)
                success = encoder.encode(input_file, output)
                if success:
                    self._log_gui("\n✅ Кодирование завершено успешно.")
                else:
                    self._log_gui("\n❌ Ошибка при кодировании.")
            else:  # decode
                decoder = YouTubeDecoder(key=key,
                                         log_callback=self._log_gui,
                                         progress_callback=self._progress_gui)
                success = decoder.decode(input_file, output)
                if success:
                    self._log_gui("\n✅ Декодирование завершено успешно.")
                else:
                    self._log_gui("\n❌ Ошибка при декодировании.")
        except Exception as e:
            self._log_gui(f"\n❌ Исключение: {e}")
            import traceback
            self._log_gui(traceback.format_exc())
        finally:
            self.after(0, self._task_finished)

    def _log_gui(self, msg):
        self.after(0, lambda: self._log(msg))

    def _progress_gui(self, percent):
        self.after(0, lambda: self._set_progress(min(100, max(0, percent))))

    def _task_finished(self):
        self.running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.progress_var.set(0)

    def _stop_task(self):
        if self.running:
            self.stop_flag = True
            self._log("⏹ Остановка по запросу... (может занять время)")
            # В текущей реализации нет возможности прервать, просто игнорируем
            self.stop_btn.config(state=tk.DISABLED)


# ========== ФУНКЦИЯ ЗАПУСКА GUI ==========
def run_gui():
    if not TKINTER_AVAILABLE:
        print("❌ Tkinter не установлен. Запустите программу из командной строки.")
        return
    app = GUIApp()
    app.mainloop()


# ========== ОСНОВНАЯ ФУНКЦИЯ ==========
def main():
    if len(sys.argv) < 2:
        # Запускаем GUI
        run_gui()
        return

    # Командная строка (без изменений)
    key = get_password_gui(
        prompt="Введите ключ шифрования (или оставьте пустым для отключения):",
        title="Ключ шифрования"
    )

    if sys.argv[1] == "encode":
        encoder = YouTubeEncoder(key)
        input_file = sys.argv[2]
        output = sys.argv[3] if len(sys.argv) > 3 else "output.mp4"
        encoder.encode(input_file, output)
    elif sys.argv[1] == "decode":
        decoder = YouTubeDecoder(key)
        video_file = sys.argv[2]
        output_dir = sys.argv[3] if len(sys.argv) > 3 else "."
        decoder.decode(video_file, output_dir)
    else:
        print(f"❌ Неизвестная команда: {sys.argv[1]}")


if __name__ == "__main__":
    main()