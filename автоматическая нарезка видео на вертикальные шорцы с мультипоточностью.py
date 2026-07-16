#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Shorts Maker PRO – автоматическая нарезка видео на вертикальные шорцы с мультипоточностью.
Особенности:
- Пакетная обработка: несколько файлов или целая папка.
- Параллельная обработка сегментов (ThreadPoolExecutor) – использует все ядра процессора.
- Сохраняет оригинальную скорость воспроизведения.
- Полный GUI с прогрессом, логом и отменой.
"""

import subprocess
import os
import sys
import math
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext


# ==================== ОСНОВНАЯ ЛОГИКА (БЕЗ GUI) ====================

def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        subprocess.run(['ffprobe', '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        raise RuntimeError("ffmpeg или ffprobe не найдены. Установите их и добавьте в PATH.")


def get_duration(file_path: str) -> float:
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
           '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    return float(output.strip())


def get_video_dimensions(file_path: str) -> tuple:
    cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
           '-show_entries', 'stream=width,height', '-of', 'csv=p=0', file_path]
    output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    width, height = map(int, output.strip().split(','))
    return width, height


def build_filter(width: int, height: int, target_w: int, target_h: int, crop: bool) -> str:
    if crop:
        if height >= width:
            scale_w = target_w / width
            scale_h = target_h / height
            scale = max(scale_w, scale_h)
            new_w = int(width * scale)
            new_h = int(height * scale)
            crop_x = max(0, (new_w - target_w) // 2)
            crop_y = max(0, (new_h - target_h) // 2)
            if new_w >= target_w and new_h >= target_h:
                return f"scale={new_w}:{new_h},crop={target_w}:{target_h}:{crop_x}:{crop_y}"
            else:
                return f"scale={target_w}:{target_h}"
        else:
            target_aspect = target_w / target_h
            crop_w = int(height * target_aspect)
            if crop_w > width:
                crop_w = width
            x_offset = max(0, (width - crop_w) // 2)
            return f"crop={crop_w}:{height}:{x_offset}:0,scale={target_w}:{target_h}"
    else:
        return f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2"


def process_segment(input_path, output_file, start, duration, filter_str, codec, audio_codec):
    """Обрабатывает один сегмент (синхронно, для вызова из потоков). Возвращает (успех, сообщение)."""
    cmd = [
        'ffmpeg',
        '-ss', str(start),
        '-i', input_path,
        '-t', str(duration),
        '-vf', filter_str,
        '-c:v', codec,
        '-c:a', audio_codec,
        '-map', '0:v:0',
        '-map', '0:a:0?',
        '-movflags', '+faststart',
        '-y',
        output_file
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True, f"✅ {output_file}"
    except subprocess.CalledProcessError as e:
        return False, f"❌ Ошибка в {output_file}: {e.stderr}"


def prepare_jobs(input_path, output_dir, segment_duration, target_w, target_h, crop, codec, audio_codec):
    """
    Подготавливает список заданий для одного видео.
    Возвращает список кортежей (input_path, output_file, start, duration, filter_str, codec, audio_codec)
    и общее количество сегментов.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Файл не найден: {input_path}")

    os.makedirs(output_dir, exist_ok=True)

    total_duration = get_duration(input_path)
    width, height = get_video_dimensions(input_path)
    num_segments = math.ceil(total_duration / segment_duration)
    filter_str = build_filter(width, height, target_w, target_h, crop)

    base_name = Path(input_path).stem
    jobs = []
    for i in range(num_segments):
        start = i * segment_duration
        duration = min(segment_duration, total_duration - start)
        if duration <= 0:
            break
        output_file = os.path.join(output_dir, f"{base_name}_part_{i+1:03d}.mp4")
        jobs.append((input_path, output_file, start, duration, filter_str, codec, audio_codec))

    return jobs, num_segments


def run_all_jobs(jobs, max_workers, log_callback, progress_callback, cancel_event):
    """
    Запускает все задания параллельно с использованием ThreadPoolExecutor.
    Возвращает (успешно_все, количество_ошибок).
    """
    total = len(jobs)
    if total == 0:
        return True, 0

    completed = 0
    errors = 0
    log_callback(f"🚀 Запуск {total} сегментов с максимальной параллельностью {max_workers}\n")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_job = {executor.submit(process_segment, *job): job for job in jobs}
        for future in as_completed(future_to_job):
            if cancel_event.is_set():
                log_callback("⏹️ Отмена операции...\n")
                executor.shutdown(wait=False, cancel_futures=True)
                return False, errors

            job = future_to_job[future]
            success, message = future.result()
            completed += 1
            if not success:
                errors += 1
            log_callback(f"[{completed}/{total}] {message}\n")
            progress_callback(completed, total)

    return errors == 0, errors


# ==================== ГРАФИЧЕСКИЙ ИНТЕРФЕЙС ====================

class ShortsMakerProApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Shorts Maker PRO – пакетная нарезка с мультипоточностью")
        self.root.geometry("800x800")
        self.root.resizable(True, True)

        # Переменные настроек
        self.output_dir = tk.StringVar(value="./shorts")
        self.segment_duration = tk.DoubleVar(value=60.0)
        self.target_width = tk.IntVar(value=1080)
        self.target_height = tk.IntVar(value=1920)
        self.no_crop = tk.BooleanVar(value=False)
        self.codec = tk.StringVar(value="libx264")
        self.audio_codec = tk.StringVar(value="aac")
        self.max_workers = tk.IntVar(value=os.cpu_count() or 4)  # автоопределение

        # Список файлов
        self.file_list = []

        self.cancel_event = threading.Event()
        self.is_running = False

        self.create_widgets()

    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ---- Список файлов ----
        file_frame = ttk.LabelFrame(main_frame, text="Видеофайлы для обработки", padding="5")
        file_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Кнопки управления списком
        btn_frame = ttk.Frame(file_frame)
        btn_frame.pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="➕ Добавить файлы", command=self.add_files).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="📁 Добавить папку", command=self.add_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="🗑️ Удалить выбранное", command=self.remove_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="🧹 Очистить всё", command=self.clear_all).pack(side=tk.LEFT, padx=2)

        # Список
        self.listbox = tk.Listbox(file_frame, selectmode=tk.EXTENDED, height=5)
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=2)

        # ---- Параметры ----
        param_frame = ttk.LabelFrame(main_frame, text="Параметры нарезки", padding="5")
        param_frame.pack(fill=tk.X, pady=5)

        # Длительность
        ttk.Label(param_frame, text="Длительность сегмента (с):").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(param_frame, textvariable=self.segment_duration, width=10).grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)

        # Разрешение
        ttk.Label(param_frame, text="Ширина:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(param_frame, textvariable=self.target_width, width=8).grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)
        ttk.Label(param_frame, text="Высота:").grid(row=1, column=2, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(param_frame, textvariable=self.target_height, width=8).grid(row=1, column=3, sticky=tk.W, padx=5, pady=2)

        # Чекбокс "Не обрезать"
        ttk.Checkbutton(param_frame, text="Не обрезать (добавить поля)", variable=self.no_crop).grid(
            row=2, column=0, columnspan=4, sticky=tk.W, padx=5, pady=2)

        # Кодеки
        ttk.Label(param_frame, text="Видеокодек:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(param_frame, textvariable=self.codec, width=15).grid(row=3, column=1, sticky=tk.W, padx=5, pady=2)
        ttk.Label(param_frame, text="Аудиокодек:").grid(row=3, column=2, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(param_frame, textvariable=self.audio_codec, width=15).grid(row=3, column=3, sticky=tk.W, padx=5, pady=2)

        # Параллельность
        ttk.Label(param_frame, text="Макс. потоков:").grid(row=4, column=0, sticky=tk.W, padx=5, pady=2)
        spin = ttk.Spinbox(param_frame, from_=1, to=64, textvariable=self.max_workers, width=8)
        spin.grid(row=4, column=1, sticky=tk.W, padx=5, pady=2)
        ttk.Label(param_frame, text=f"(авто: {os.cpu_count() or 4} ядер)").grid(row=4, column=2, sticky=tk.W, padx=5, pady=2)

        # Выходная папка
        out_frame = ttk.LabelFrame(main_frame, text="Выходная папка", padding="5")
        out_frame.pack(fill=tk.X, pady=5)
        ttk.Entry(out_frame, textvariable=self.output_dir, width=50).pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        ttk.Button(out_frame, text="Обзор...", command=self.browse_output).pack(side=tk.RIGHT, padx=5)

        # ---- Кнопки управления ----
        btn_control = ttk.Frame(main_frame)
        btn_control.pack(fill=tk.X, pady=10)

        self.start_btn = ttk.Button(btn_control, text="▶ Начать нарезку", command=self.start_processing)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.cancel_btn = ttk.Button(btn_control, text="⏹ Отмена", command=self.cancel_processing, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(btn_control, text="Очистить лог", command=self.clear_log).pack(side=tk.RIGHT, padx=5)

        # ---- Прогресс ----
        self.progress_var = tk.IntVar()
        self.progress_bar = ttk.Progressbar(main_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=5)

        self.progress_label = ttk.Label(main_frame, text="Готов к работе")
        self.progress_label.pack()

        # ---- Лог ----
        log_frame = ttk.LabelFrame(main_frame, text="Лог выполнения", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, wrap=tk.WORD, font=("Courier", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # ---- Обработчики списка файлов ----
    def add_files(self):
        files = filedialog.askopenfilenames(
            title="Выберите видеофайлы",
            filetypes=[("Видео файлы", "*.mp4 *.avi *.mov *.mkv *.flv *.webm"), ("Все файлы", "*.*")]
        )
        for f in files:
            if f not in self.file_list:
                self.file_list.append(f)
                self.listbox.insert(tk.END, f)

    def add_folder(self):
        folder = filedialog.askdirectory(title="Выберите папку с видео")
        if folder:
            for f in Path(folder).glob("*"):
                if f.suffix.lower() in ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.webm']:
                    if str(f) not in self.file_list:
                        self.file_list.append(str(f))
                        self.listbox.insert(tk.END, str(f))

    def remove_selected(self):
        selected = self.listbox.curselection()
        for index in reversed(selected):
            del self.file_list[index]
            self.listbox.delete(index)

    def clear_all(self):
        self.file_list.clear()
        self.listbox.delete(0, tk.END)

    def browse_output(self):
        dirname = filedialog.askdirectory(title="Выберите папку для сохранения")
        if dirname:
            self.output_dir.set(dirname)

    # ---- Логирование и прогресс ----
    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def log_callback(self, message):
        self.log_text.insert(tk.END, message)
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def progress_callback(self, completed, total):
        percent = int((completed / total) * 100) if total > 0 else 0
        self.progress_var.set(percent)
        self.progress_label.config(text=f"Обработано {completed} из {total} сегментов")
        self.root.update_idletasks()

    # ---- Запуск и отмена ----
    def start_processing(self):
        if self.is_running:
            return
        if not self.file_list:
            messagebox.showerror("Ошибка", "Добавьте хотя бы один видеофайл.")
            return
        if not self.output_dir.get():
            messagebox.showerror("Ошибка", "Укажите выходную папку.")
            return

        # Проверка ffmpeg
        try:
            check_ffmpeg()
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))
            return

        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.is_running = True
        self.cancel_event.clear()

        self.progress_var.set(0)
        self.progress_label.config(text="Подготовка заданий...")
        self.log_callback("=" * 60 + "\n")
        self.log_callback(f"🚀 Запуск: {time.ctime()}\n")
        self.log_callback(f"Файлов: {len(self.file_list)}\n")
        self.log_callback(f"Выходная папка: {self.output_dir.get()}\n")
        self.log_callback(f"Параллельных задач: {self.max_workers.get()}\n")

        # Запускаем в отдельном потоке
        thread = threading.Thread(target=self.run_processing, daemon=True)
        thread.start()

    def run_processing(self):
        try:
            # Собираем все задания со всех файлов
            all_jobs = []
            total_segments = 0
            for file_path in self.file_list:
                try:
                    jobs, num = prepare_jobs(
                        input_path=file_path,
                        output_dir=self.output_dir.get(),
                        segment_duration=self.segment_duration.get(),
                        target_w=self.target_width.get(),
                        target_h=self.target_height.get(),
                        crop=not self.no_crop.get(),
                        codec=self.codec.get(),
                        audio_codec=self.audio_codec.get()
                    )
                    all_jobs.extend(jobs)
                    total_segments += num
                except Exception as e:
                    self.log_callback(f"❌ Ошибка при подготовке {file_path}: {e}\n")

            if not all_jobs:
                self.log_callback("❌ Нет сегментов для обработки.\n")
                self.progress_label.config(text="❌ Ошибка")
                return

            self.log_callback(f"📊 Всего сегментов для обработки: {len(all_jobs)}\n")

            # Запускаем параллельную обработку
            success, errors = run_all_jobs(
                jobs=all_jobs,
                max_workers=self.max_workers.get(),
                log_callback=self.log_callback,
                progress_callback=self.progress_callback,
                cancel_event=self.cancel_event
            )

            if self.cancel_event.is_set():
                self.progress_label.config(text="⏹ Отменено")
            elif success:
                self.progress_label.config(text="✅ Готово!")
            else:
                self.progress_label.config(text=f"⚠️ Завершено с {errors} ошибками")

        except Exception as e:
            self.log_callback(f"❌ Критическая ошибка: {e}\n")
            self.progress_label.config(text="❌ Ошибка!")
        finally:
            self.root.after(0, self.finish_processing)

    def finish_processing(self):
        self.start_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        self.is_running = False
        self.log_callback("=" * 60 + "\n")

    def cancel_processing(self):
        if self.is_running:
            self.log_callback("⏳ Запрос отмены...\n")
            self.cancel_event.set()
            self.cancel_btn.config(state=tk.DISABLED)


# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    root = tk.Tk()
    app = ShortsMakerProApp(root)
    root.mainloop()