import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import cv2
from PIL import Image, ImageTk
import time
import os
import json
import threading
from datetime import datetime
import platform
import pyaudio
import wave
import subprocess
import tempfile
import sys
import ctypes
import pygame  # Добавляем pygame для воспроизведения звука

class VideoRecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Видеорегистратор + Фото/Видеоловушка")
        self.root.geometry("1100x850")
        self.root.minsize(900, 700)
        self.root.configure(bg='#0f0f1f')
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # ---------- Базовые параметры ----------
        self.cap = None
        self.camera_index = 0
        self.output_path = os.path.join(os.path.expanduser("~"), "Videos")
        if not os.path.exists(self.output_path):
            os.makedirs(self.output_path)

        self.target_fps = 30
        self.actual_fps = 30
        self.codec = "XVID"
        self.extension = ".avi"

        self.display_width = 800
        self.display_height = 0
        self.last_frame = None
        self.frame_lock = threading.Lock()

        # ---------- Состояния записи ----------
        self.is_recording = False
        self.video_writer = None
        self.motion_recording = False
        self.motion_timer_id = None
        self.current_video_path = None
        self.recording_time = 0
        self.motion_record_duration = 5 * 60

        # ---------- Параметры ловушек ----------
        self.photo_trap_enabled = False
        self.video_trap_enabled = False
        self.prev_gray = None
        self.motion_threshold = 200
        self.motion_cooldown = 2.0
        self.last_motion_time = 0
        self.motion_counter = 0
        self.motion_reduce_factor = 9
        self.photos_folder = os.path.join(self.output_path, "Photos")
        if not os.path.exists(self.photos_folder):
            os.makedirs(self.photos_folder)
        
        # ---------- Звуковые параметры ----------
        self.sound_enabled = True
        self.sound_volume = 0.7
        self.custom_sound_path = None
        self.default_sound_path = None
        self.sound_thread = None
        self.sound_playing = False
        
        # Создаем стандартный звук (бип) если нет файла
        self.create_default_sound()
        
        # ---------- Улучшенные параметры детекции ----------
        self.motion_history = []
        self.consecutive_motion = 0
        self.min_consecutive_frames = 3
        self.motion_blur_size = 31
        self.motion_diff_threshold = 30
        self.min_motion_area = 500
        
        # ---------- Статистика файлов ----------
        self.total_photos_saved = 0
        self.total_videos_saved = 0
        self.scan_existing_files()

        # ---------- Настройки системы ----------
        self.prevent_sleep = True
        self.screen_off_enabled = False
        self.screen_off_timer = None
        self.auto_start = False

        # ---------- Аудио-параметры ----------
        self.audio_enabled = True
        self.audio_frames = []
        self.audio_stream = None
        self.audio_thread = None
        self.audio_recording = False
        self.audio_sample_rate = 44100
        self.audio_channels = 2
        self.audio_format = pyaudio.paInt16
        self.audio_chunk = 1024

        self.capture_running = True
        self.theme = 'dark'
        self.color_scheme = {
            'bg': '#0f0f1f',
            'bg2': '#1a1a2e',
            'bg3': '#16213e',
            'accent': '#e94560',
            'accent2': '#00b894',
            'accent3': '#ffd93d',
            'text': '#ffffff',
            'text2': '#8899aa',
            'text3': '#6c757d'
        }

        # Инициализация pygame для звука
        try:
            pygame.mixer.init()
        except:
            print("Ошибка инициализации звука")

        self.create_widgets()
        self.connect_camera()
        self.update_clock()
        
        # Запускаем системные функции
        self.start_system_functions()

    def create_default_sound(self):
        """Создание стандартного звукового сигнала"""
        try:
            # Создаем временный WAV файл с простым бипом
            import numpy as np
            import wave
            
            # Параметры звука
            sample_rate = 44100
            duration = 0.3  # секунды
            frequency = 800  # Гц
            
            # Генерируем синусоиду
            t = np.linspace(0, duration, int(sample_rate * duration))
            wave_data = np.sin(2 * np.pi * frequency * t) * 0.5
            
            # Преобразуем в 16-bit
            wave_data = (wave_data * 32767).astype(np.int16)
            
            # Сохраняем во временный файл
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
                self.default_sound_path = tmp_file.name
                with wave.open(self.default_sound_path, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sample_rate)
                    wf.writeframes(wave_data.tobytes())
        except Exception as e:
            print(f"Ошибка создания стандартного звука: {e}")

    def play_sound(self):
        """Воспроизведение звука при обнаружении движения"""
        if not self.sound_enabled:
            return
        
        # Предотвращаем множественное воспроизведение
        if self.sound_playing:
            return
        
        def play():
            try:
                self.sound_playing = True
                
                # Выбираем звуковой файл
                sound_file = self.custom_sound_path if self.custom_sound_path else self.default_sound_path
                
                if sound_file and os.path.exists(sound_file):
                    # Воспроизводим через pygame
                    pygame.mixer.music.load(sound_file)
                    pygame.mixer.music.set_volume(self.sound_volume)
                    pygame.mixer.music.play()
                    
                    # Ждем окончания воспроизведения
                    while pygame.mixer.music.get_busy():
                        time.sleep(0.1)
                else:
                    # Если файл не найден, используем системный бип
                    if platform.system() == "Windows":
                        import winsound
                        winsound.Beep(800, 300)
                    else:
                        print('\a')  # ASCII bell
                
            except Exception as e:
                print(f"Ошибка воспроизведения звука: {e}")
            finally:
                self.sound_playing = False
        
        # Запускаем в отдельном потоке
        self.sound_thread = threading.Thread(target=play, daemon=True)
        self.sound_thread.start()

    def start_system_functions(self):
        """Запуск системных функций (защита от сна, автостарт)"""
        if self.prevent_sleep:
            self.disable_sleep_mode()
        
        if self.auto_start:
            self.add_to_startup()

    def disable_sleep_mode(self):
        """Отключение спящего режима"""
        try:
            if platform.system() == "Windows":
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000002)
                self.status_text.config(text="● Спящий режим отключен", fg=self.color_scheme['accent2'])
            elif platform.system() == "Linux":
                subprocess.Popen(["systemd-inhibit", "--what=sleep", "--why=Video Recording", 
                                 "sleep", "infinity"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.status_text.config(text="● Спящий режим отключен", fg=self.color_scheme['accent2'])
            elif platform.system() == "Darwin":
                subprocess.Popen(["caffeinate", "-d"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.status_text.config(text="● Спящий режим отключен", fg=self.color_scheme['accent2'])
        except Exception as e:
            print(f"Ошибка отключения спящего режима: {e}")

    def toggle_screen_off(self):
        """Включение/выключение гашения экрана"""
        self.screen_off_enabled = not self.screen_off_enabled
        if self.screen_off_enabled:
            self.turn_off_screen()
            self.screen_off_btn.config(text="💡 ВКЛЮЧИТЬ ЭКРАН", bg=self.color_scheme['accent2'])
            self.status_text.config(text="● Экран погашен", fg=self.color_scheme['accent2'])
        else:
            self.turn_on_screen()
            self.screen_off_btn.config(text="💡 ПОГАСИТЬ ЭКРАН", bg='#6c757d')
            self.status_text.config(text="● Экран включен", fg=self.color_scheme['text2'])

    def turn_off_screen(self):
        """Гашение экрана"""
        try:
            if platform.system() == "Windows":
                ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
            elif platform.system() == "Linux":
                subprocess.Popen(["xset", "dpms", "force", "off"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif platform.system() == "Darwin":
                subprocess.Popen(["pmset", "displaysleepnow"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Ошибка гашения экрана: {e}")

    def turn_on_screen(self):
        """Включение экрана"""
        try:
            if platform.system() == "Windows":
                ctypes.windll.user32.mouse_event(0x0001, 0, 1, 0, 0)
            elif platform.system() == "Linux":
                subprocess.Popen(["xset", "dpms", "force", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Ошибка включения экрана: {e}")

    def add_to_startup(self):
        """Добавление программы в автозагрузку"""
        try:
            if platform.system() == "Windows":
                import winreg
                key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
                winreg.SetValueEx(key, "VideoRecorderPro", 0, winreg.REG_SZ, sys.executable + " " + __file__)
                winreg.CloseKey(key)
                self.status_text.config(text="● Добавлено в автозагрузку", fg=self.color_scheme['accent2'])
            elif platform.system() == "Linux":
                desktop_path = os.path.expanduser("~/.config/autostart/videorecorder.desktop")
                with open(desktop_path, 'w') as f:
                    f.write(f"""[Desktop Entry]
Type=Application
Name=VideoRecorderPro
Exec={sys.executable} {__file__}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
""")
                self.status_text.config(text="● Добавлено в автозагрузку", fg=self.color_scheme['accent2'])
        except Exception as e:
            print(f"Ошибка добавления в автозагрузку: {e}")

    def scan_existing_files(self):
        try:
            video_files = [f for f in os.listdir(self.output_path) 
                          if f.endswith(('.avi', '.mp4', '.mov', '.mkv'))]
            self.total_videos_saved = len(video_files)
            
            photo_files = [f for f in os.listdir(self.photos_folder) 
                          if f.endswith(('.jpg', '.jpeg', '.png'))]
            self.total_photos_saved = len(photo_files)
        except:
            self.total_videos_saved = 0
            self.total_photos_saved = 0

    # ------------------ Интерфейс ------------------
    def create_widgets(self):
        self.main_container = tk.Frame(self.root, bg=self.color_scheme['bg'])
        self.main_container.pack(fill=tk.BOTH, expand=True)

        self.create_toolbar()
        self.create_main_content()
        self.create_statusbar()

    def create_toolbar(self):
        toolbar = tk.Frame(self.main_container, bg=self.color_scheme['bg2'], height=50)
        toolbar.pack(fill=tk.X)
        toolbar.pack_propagate(False)

        logo_frame = tk.Frame(toolbar, bg=self.color_scheme['bg2'])
        logo_frame.pack(side=tk.LEFT, padx=15)
        
        tk.Label(logo_frame, text="🎥", font=('Arial', 20), 
                bg=self.color_scheme['bg2']).pack(side=tk.LEFT)
        tk.Label(logo_frame, text="Видеорегистратор Pro", font=('Arial', 14, 'bold'),
                bg=self.color_scheme['bg2'], fg=self.color_scheme['accent']).pack(side=tk.LEFT, padx=8)
        tk.Label(logo_frame, text="v3.0", font=('Arial', 9),
                bg=self.color_scheme['bg2'], fg=self.color_scheme['text2']).pack(side=tk.LEFT)

        center_frame = tk.Frame(toolbar, bg=self.color_scheme['bg2'])
        center_frame.pack(side=tk.LEFT, expand=True)

        self.btn_start = self.create_modern_button(center_frame, "▶ ЗАПИСЬ", 
                                                   self.start_recording, '#e94560', '#ff6b6b')
        self.btn_start.pack(side=tk.LEFT, padx=3)
        
        self.btn_pause = self.create_modern_button(center_frame, "⏸ ПАУЗА", 
                                                   self.toggle_pause, '#6c757d', '#868e96', state='disabled')
        self.btn_pause.pack(side=tk.LEFT, padx=3)
        
        self.btn_stop = self.create_modern_button(center_frame, "⏹ СТОП", 
                                                  self.stop_recording, '#6c757d', '#868e96', state='disabled')
        self.btn_stop.pack(side=tk.LEFT, padx=3)

        timer_frame = tk.Frame(center_frame, bg=self.color_scheme['bg2'])
        timer_frame.pack(side=tk.LEFT, padx=15)
        
        self.timer_label = tk.Label(timer_frame, text="00:00:00", 
                                    font=('Arial', 20, 'bold'),
                                    bg=self.color_scheme['bg2'], fg=self.color_scheme['accent2'])
        self.timer_label.pack()

        right_frame = tk.Frame(toolbar, bg=self.color_scheme['bg2'])
        right_frame.pack(side=tk.RIGHT, padx=10)
        
        self.screen_off_btn = self.create_modern_button(
            right_frame, "💡 ПОГАСИТЬ ЭКРАН", 
            self.toggle_screen_off, '#6c757d', '#868e96'
        )
        self.screen_off_btn.pack(side=tk.LEFT, padx=3)
        
        self.btn_settings = self.create_modern_button(
            right_frame, "⚙ НАСТРОЙКИ", 
            self.open_settings, '#00b894', '#00d2d3'
        )
        self.btn_settings.pack(side=tk.LEFT, padx=3)

    def create_modern_button(self, parent, text, command, color, hover_color, state='normal'):
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=color,
            fg='white',
            font=('Arial', 10, 'bold'),
            padx=15,
            pady=6,
            relief=tk.FLAT,
            cursor='hand2',
            bd=0,
            state=state
        )
        
        def on_enter(e):
            if btn['state'] != 'disabled':
                btn['bg'] = hover_color
        def on_leave(e):
            if btn['state'] != 'disabled':
                btn['bg'] = color
                
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return btn

    def create_main_content(self):
        main_frame = tk.Frame(self.main_container, bg=self.color_scheme['bg'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left_frame = tk.Frame(main_frame, bg=self.color_scheme['bg'])
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        video_container = tk.Frame(left_frame, bg=self.color_scheme['bg3'], relief=tk.RIDGE, bd=2)
        video_container.pack(fill=tk.BOTH, expand=True)
        
        self.video_label = tk.Label(video_container, bg=self.color_scheme['bg3'])
        self.video_label.pack(fill=tk.BOTH, expand=True)
        
        self.create_video_overlay(video_container)

        right_frame = tk.Frame(main_frame, bg=self.color_scheme['bg'], width=350)
        right_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))
        right_frame.pack_propagate(False)

        self.create_control_panel(right_frame)
        self.create_trap_panel(right_frame)

    def create_video_overlay(self, parent):
        self.record_indicator = tk.Label(parent, text="", font=('Arial', 16, 'bold'),
                                         bg=self.color_scheme['bg3'], fg='#ff0000')
        self.record_indicator.place(x=10, y=10)
        
        self.record_timer = tk.Label(parent, text="00:00:00", font=('Arial', 14, 'bold'),
                                     bg=self.color_scheme['bg3'], fg='white')
        self.record_timer.place(x=10, y=40)
        
        self.motion_indicator = tk.Label(parent, text="", font=('Arial', 12, 'bold'),
                                         bg=self.color_scheme['bg3'], fg='#ff6b6b')
        self.motion_indicator.place(relx=0.5, y=15, anchor='n')
        
        self.video_info = tk.Label(parent, text="", font=('Arial', 9),
                                   bg=self.color_scheme['bg3'], fg=self.color_scheme['text2'])
        self.video_info.place(relx=1.0, x=-10, y=10, anchor='ne')

    def create_control_panel(self, parent):
        control_frame = tk.LabelFrame(parent, text=" Управление ", 
                                      font=('Arial', 11, 'bold'),
                                      bg=self.color_scheme['bg2'], fg='white', 
                                      relief=tk.FLAT)
        control_frame.pack(fill=tk.X, pady=5)
        
        control_inner = tk.Frame(control_frame, bg=self.color_scheme['bg2'])
        control_inner.pack(pady=8, padx=8)
        
        self.recording_status = tk.Label(
            control_inner,
            text="● Ожидание",
            font=('Arial', 10),
            bg=self.color_scheme['bg2'],
            fg=self.color_scheme['text2']
        )
        self.recording_status.pack(pady=5)

    def create_trap_panel(self, parent):
        trap_frame = tk.LabelFrame(parent, text=" 🎯 ЛОВУШКИ ", 
                                   font=('Arial', 11, 'bold'),
                                   bg=self.color_scheme['bg2'], fg='white', 
                                   relief=tk.FLAT)
        trap_frame.pack(fill=tk.X, pady=5)
        
        trap_inner = tk.Frame(trap_frame, bg=self.color_scheme['bg2'])
        trap_inner.pack(pady=8, padx=8)
        
        # Фотоловушка
        self.btn_photo_trap = self.create_toggle_button(
            trap_inner, "📸 ФОТОЛОВУШКА", self.toggle_photo_trap, '#6c757d'
        )
        self.btn_photo_trap.pack(fill=tk.X, pady=2)
        
        # Видеоловушка
        self.btn_video_trap = self.create_toggle_button(
            trap_inner, "🎥 ВИДЕОЛОВУШКА (5 мин)", self.toggle_video_trap, '#6c757d'
        )
        self.btn_video_trap.pack(fill=tk.X, pady=2)
        
        # Информация о чувствительности
        sens_info = tk.Label(
            trap_inner,
            text=f"🔍 Чувствительность: {self.motion_threshold}",
            font=('Arial', 8),
            bg=self.color_scheme['bg2'],
            fg=self.color_scheme['text2']
        )
        sens_info.pack(anchor='w', pady=2)
        
        # Статистика файлов
        stats_frame = tk.Frame(trap_inner, bg=self.color_scheme['bg2'])
        stats_frame.pack(fill=tk.X, pady=5)
        
        self.photo_counter_label = tk.Label(
            stats_frame,
            text=f"📸 Фото: {self.total_photos_saved}",
            font=('Arial', 9, 'bold'),
            bg=self.color_scheme['bg2'],
            fg=self.color_scheme['accent3']
        )
        self.photo_counter_label.pack(anchor='w', pady=1)
        
        self.video_counter_label = tk.Label(
            stats_frame,
            text=f"🎬 Видео: {self.total_videos_saved}",
            font=('Arial', 9, 'bold'),
            bg=self.color_scheme['bg2'],
            fg=self.color_scheme['accent3']
        )
        self.video_counter_label.pack(anchor='w', pady=1)
        
        total_files = self.total_photos_saved + self.total_videos_saved
        self.total_counter_label = tk.Label(
            stats_frame,
            text=f"📁 Всего файлов: {total_files}",
            font=('Arial', 9, 'bold'),
            bg=self.color_scheme['bg2'],
            fg=self.color_scheme['accent']
        )
        self.total_counter_label.pack(anchor='w', pady=1)
        
        status_frame = tk.Frame(trap_inner, bg=self.color_scheme['bg2'])
        status_frame.pack(fill=tk.X, pady=5)
        
        self.photo_status = tk.Label(status_frame, text="📷 выкл", 
                                     font=('Arial', 9), bg=self.color_scheme['bg2'], 
                                     fg=self.color_scheme['text3'])
        self.photo_status.pack(side=tk.LEFT, padx=5)
        
        self.video_status = tk.Label(status_frame, text="🎬 выкл",
                                     font=('Arial', 9), bg=self.color_scheme['bg2'],
                                     fg=self.color_scheme['text3'])
        self.video_status.pack(side=tk.LEFT, padx=15)
        
        # Индикатор звука
        self.sound_status = tk.Label(
            status_frame,
            text="🔊 звук вкл",
            font=('Arial', 9),
            bg=self.color_scheme['bg2'],
            fg=self.color_scheme['accent2']
        )
        self.sound_status.pack(side=tk.LEFT, padx=15)
        
        refresh_stats_btn = tk.Button(
            trap_inner,
            text="🔄 Обновить статистику",
            command=self.refresh_statistics,
            bg=self.color_scheme['bg3'],
            fg=self.color_scheme['text'],
            font=('Arial', 8),
            relief=tk.FLAT,
            cursor='hand2'
        )
        refresh_stats_btn.pack(fill=tk.X, pady=2)

    def create_toggle_button(self, parent, text, command, color):
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=color,
            fg='white',
            font=('Arial', 9, 'bold'),
            padx=10,
            pady=6,
            relief=tk.FLAT,
            cursor='hand2'
        )
        return btn

    def create_statusbar(self):
        statusbar = tk.Frame(self.main_container, bg=self.color_scheme['bg2'], height=28)
        statusbar.pack(side=tk.BOTTOM, fill=tk.X)
        statusbar.pack_propagate(False)
        
        self.status_text = tk.Label(statusbar, text="● Готов к работе",
                                    font=('Arial', 9), bg=self.color_scheme['bg2'],
                                    fg=self.color_scheme['accent2'])
        self.status_text.pack(side=tk.LEFT, padx=10)
        
        self.file_status = tk.Label(statusbar, text="",
                                    font=('Arial', 9), bg=self.color_scheme['bg2'],
                                    fg=self.color_scheme['text2'])
        self.file_status.pack(side=tk.LEFT, padx=10, expand=True)
        
        self.fps_status = tk.Label(statusbar, text="FPS: 30",
                                   font=('Arial', 9), bg=self.color_scheme['bg2'],
                                   fg=self.color_scheme['text3'])
        self.fps_status.pack(side=tk.RIGHT, padx=10)

    def update_clock(self):
        current_time = datetime.now().strftime("%H:%M:%S")
        self.root.after(1000, self.update_clock)

    def refresh_statistics(self):
        self.scan_existing_files()
        self.update_statistics_display()

    def update_statistics_display(self):
        self.photo_counter_label.config(text=f"📸 Фото: {self.total_photos_saved}")
        self.video_counter_label.config(text=f"🎬 Видео: {self.total_videos_saved}")
        total_files = self.total_photos_saved + self.total_videos_saved
        self.total_counter_label.config(text=f"📁 Всего файлов: {total_files}")

    # ------------------ Основные функции ------------------
    def add_timestamp_to_frame(self, frame):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, timestamp, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                    0.8, (0, 255, 255), 2, cv2.LINE_AA)
        if self.is_recording:
            cv2.putText(frame, "● REC", (10, 55), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 0, 255), 2, cv2.LINE_AA)
        elif self.motion_recording:
            cv2.putText(frame, "● AUTO (5 мин)", (10, 55), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 255, 255), 2, cv2.LINE_AA)
        return frame

    def connect_camera(self, try_indices=None):
        if try_indices is None:
            try_indices = [self.camera_index, 0, 1, 2]

        if self.cap is not None and self.cap.isOpened():
            self.cap.release()
            self.cap = None

        api_preference = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY

        for idx in set(try_indices):
            if idx < 0:
                continue
            cap = cv2.VideoCapture(idx, api_preference)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    self.width, self.height = self.get_max_resolution(cap)
                    cap.set(cv2.CAP_PROP_FPS, self.target_fps)
                    real_fps = self.measure_real_fps(cap, 30)
                    self.actual_fps = real_fps

                    self.cap = cap
                    self.camera_index = idx
                    self.display_height = int(self.display_width * self.height / self.width)
                    self.fps_status.config(text=f"FPS: {real_fps:.1f}")
                    self.video_info.config(text=f"{self.width}x{self.height}")

                    self.capture_running = True
                    self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
                    self.capture_thread.start()
                    self.update_display()
                    return True
                else:
                    cap.release()

        if not hasattr(self, '_error_shown'):
            self._error_shown = True
            messagebox.showerror("Ошибка", "Не удалось открыть камеру")
        return False

    def get_max_resolution(self, cap):
        resolutions = [(3840, 2160), (2560, 1440), (1920, 1080),
                      (1280, 720), (1024, 768), (800, 600), (640, 480)]
        for w, h in resolutions:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if abs(aw - w) < 10 and abs(ah - h) < 10:
                return aw, ah
        return int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    def measure_real_fps(self, cap, num_frames=30):
        start_time = time.time()
        for _ in range(num_frames):
            ret, frame = cap.read()
            if not ret or frame is None:
                break
        elapsed = time.time() - start_time
        return num_frames / elapsed if elapsed > 0 else 30.0

    def capture_loop(self):
        while self.capture_running and self.cap is not None and self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret or frame is None:
                time.sleep(0.01)
                continue

            frame_with_time = self.add_timestamp_to_frame(frame)

            if self.video_writer is not None:
                self.video_writer.write(frame_with_time)
                if self.is_recording or self.motion_recording:
                    self.recording_time += 1/self.actual_fps
                    self.root.after(0, self.update_timer)

            if self.photo_trap_enabled or self.video_trap_enabled:
                self.detect_motion(frame_with_time)

            with self.frame_lock:
                self.last_frame = frame_with_time.copy()

            time.sleep(0.001)

    def update_display(self):
        if self.last_frame is not None:
            with self.frame_lock:
                frame = self.last_frame.copy()
            display_frame = cv2.resize(frame, (self.display_width, self.display_height), 
                                     interpolation=cv2.INTER_LANCZOS4)
            frame_rgb = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
            imgtk = ImageTk.PhotoImage(image=img)
            self.video_label.imgtk = imgtk
            self.video_label.config(image=imgtk)

        self.root.after(30, self.update_display)

    def update_timer(self):
        hours = int(self.recording_time // 3600)
        minutes = int((self.recording_time % 3600) // 60)
        seconds = int(self.recording_time % 60)
        time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        self.timer_label.config(text=time_str)
        self.record_timer.config(text=time_str)

    # ------------------ Детекция движения со звуком ------------------
    def detect_motion(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (self.motion_blur_size, self.motion_blur_size), 0)

        if self.prev_gray is None:
            self.prev_gray = gray
            return

        diff = cv2.absdiff(self.prev_gray, gray)
        thresh = cv2.threshold(diff, self.motion_diff_threshold, 255, cv2.THRESH_BINARY)[1]
        
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        significant_motion = False
        total_motion_area = 0
        
        for contour in contours:
            area = cv2.contourArea(contour)
            if area > self.min_motion_area:
                significant_motion = True
                total_motion_area += area
        
        if significant_motion:
            self.consecutive_motion += 1
            self.motion_history.append(total_motion_area)
            if len(self.motion_history) > 10:
                self.motion_history.pop(0)
            
            if self.consecutive_motion >= self.min_consecutive_frames:
                avg_area = sum(self.motion_history) / len(self.motion_history)
                self.motion_counter += 1
                
                if self.motion_counter % self.motion_reduce_factor == 0:
                    current_time = time.time()
                    if current_time - self.last_motion_time >= self.motion_cooldown:
                        self.last_motion_time = current_time
                        
                        # Воспроизводим звук
                        self.play_sound()
                        
                        self.root.after(0, lambda: self.motion_indicator.config(
                            text="⚠ ДВИЖЕНИЕ! 🔊", fg='#ff6b6b'
                        ))
                        self.root.after(2000, lambda: self.motion_indicator.config(text=""))

                        if self.photo_trap_enabled:
                            self.save_photo(frame)
                            self.total_photos_saved += 1
                            self.root.after(0, self.update_statistics_display)
                            self.photo_status.config(text="📷 сохранено! 🔊", fg='#ffd93d')
                            self.root.after(2000, lambda: self.photo_status.config(
                                text="📷 активна" if self.photo_trap_enabled else "📷 выкл",
                                fg=self.color_scheme['accent2'] if self.photo_trap_enabled else self.color_scheme['text3']
                            ))

                        if self.video_trap_enabled and not self.is_recording and not self.motion_recording:
                            self.root.after(0, self.start_motion_recording)
        else:
            self.consecutive_motion = 0
            self.motion_history = []

        self.prev_gray = gray

    def save_photo(self, frame):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"motion_photo_{timestamp}.jpg"
        filepath = os.path.join(self.photos_folder, filename)
        cv2.imwrite(filepath, frame)
        self.file_status.config(text=f"📸 {filename}")

    # ------------------ Аудио-функции ------------------
    def start_audio_recording(self):
        if not self.audio_enabled:
            return
        self.audio_recording = True
        self.audio_frames = []
        self.audio_thread = threading.Thread(target=self._record_audio, daemon=True)
        self.audio_thread.start()

    def _record_audio(self):
        p = pyaudio.PyAudio()
        self.audio_stream = p.open(format=self.audio_format,
                                   channels=self.audio_channels,
                                   rate=self.audio_sample_rate,
                                   input=True,
                                   frames_per_buffer=self.audio_chunk)
        while self.audio_recording:
            data = self.audio_stream.read(self.audio_chunk, exception_on_overflow=False)
            self.audio_frames.append(data)
        self.audio_stream.stop_stream()
        self.audio_stream.close()
        p.terminate()

    def stop_audio_recording(self, video_path):
        if not self.audio_enabled or not self.audio_frames:
            return video_path
        self.audio_recording = False
        if self.audio_thread is not None:
            self.audio_thread.join(timeout=1.0)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
            wav_path = tmp_wav.name
        wf = wave.open(wav_path, 'wb')
        wf.setnchannels(self.audio_channels)
        wf.setsampwidth(pyaudio.PyAudio().get_sample_size(self.audio_format))
        wf.setframerate(self.audio_sample_rate)
        wf.writeframes(b''.join(self.audio_frames))
        wf.close()

        output_with_audio = video_path.replace(self.extension, "_with_audio" + self.extension)
        cmd = ["ffmpeg", "-y", "-i", video_path, "-i", wav_path,
               "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0", "-shortest", output_with_audio]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            os.remove(video_path)
            os.rename(output_with_audio, video_path)
            self.file_status.config(text=f"🎵 {os.path.basename(video_path)}")
        except Exception as e:
            print(f"Ошибка: {e}")
        finally:
            if os.path.exists(wav_path):
                os.remove(wav_path)
        return video_path

    # ------------------ Запись ------------------
    def start_recording(self):
        if self.is_recording:
            return
        if self.motion_recording:
            self.stop_motion_recording()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"recording_{timestamp}{self.extension}"
        filepath = os.path.join(self.output_path, filename)

        fourcc = cv2.VideoWriter_fourcc(*self.codec)
        self.video_writer = cv2.VideoWriter(filepath, fourcc, self.actual_fps, (self.width, self.height))
        if not self.video_writer.isOpened():
            messagebox.showerror("Ошибка", "Не удалось создать файл записи")
            return

        self.is_recording = True
        self.current_video_path = filepath
        self.recording_time = 0
        self.start_audio_recording()
        
        self.record_indicator.config(text="● REC", fg='#ff0000')
        self.recording_status.config(text="● Идет запись...", fg='#ff6b6b')
        self.status_text.config(text="● Идет запись...", fg='#ff6b6b')
        self.file_status.config(text=f"💾 {os.path.basename(filepath)}")
        self.btn_start.config(state='disabled', bg='#4a4a4a')
        self.btn_stop.config(state='normal', bg='#e94560')
        self.btn_pause.config(state='normal', bg='#ffd93d')

    def toggle_pause(self):
        self.status_text.config(text="● Пауза", fg='#ffd93d')

    def stop_recording(self):
        if self.is_recording:
            self.is_recording = False
            if self.video_writer is not None:
                self.video_writer.release()
                self.video_writer = None
            self.stop_audio_recording(self.current_video_path)
            
            self.total_videos_saved += 1
            self.root.after(0, self.update_statistics_display)
            
            self.record_indicator.config(text="", fg='#444')
            self.timer_label.config(text="00:00:00")
            self.record_timer.config(text="00:00:00")
            self.recording_status.config(text="● Ожидание", fg=self.color_scheme['text2'])
            self.status_text.config(text="● Запись остановлена", fg=self.color_scheme['accent2'])
            self.file_status.config(text="💾 Файл сохранен")
            self.btn_start.config(state='normal', bg='#e94560')
            self.btn_stop.config(state='disabled', bg='#6c757d')
            self.btn_pause.config(state='disabled', bg='#6c757d')
        elif self.motion_recording:
            self.stop_motion_recording()

    def start_motion_recording(self):
        if self.motion_recording or self.is_recording:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"motion_video_{timestamp}{self.extension}"
        filepath = os.path.join(self.output_path, filename)

        fourcc = cv2.VideoWriter_fourcc(*self.codec)
        self.video_writer = cv2.VideoWriter(filepath, fourcc, self.actual_fps, (self.width, self.height))
        if not self.video_writer.isOpened():
            messagebox.showerror("Ошибка", "Не удалось создать файл для видео-ловушки")
            return

        self.motion_recording = True
        self.current_video_path = filepath
        self.recording_time = 0
        self.start_audio_recording()
        
        self.motion_timer_id = self.root.after(self.motion_record_duration * 1000, self.stop_motion_recording)
        
        self.record_indicator.config(text="● AUTO (5 мин)", fg='#ffd93d')
        self.recording_status.config(text="● Авто-запись (5 мин)", fg='#ffd93d')
        self.status_text.config(text="● Авто-запись (5 мин)", fg='#ffd93d')
        self.file_status.config(text=f"🎥 {os.path.basename(filepath)}")
        self.btn_start.config(state='disabled', bg='#4a4a4a')
        self.btn_stop.config(state='normal', bg='#e94560')

    def stop_motion_recording(self):
        if not self.motion_recording:
            return
        if self.motion_timer_id is not None:
            self.root.after_cancel(self.motion_timer_id)
            self.motion_timer_id = None
        self.motion_recording = False
        if self.video_writer is not None:
            self.video_writer.release()
            self.video_writer = None
        self.stop_audio_recording(self.current_video_path)
        
        self.total_videos_saved += 1
        self.root.after(0, self.update_statistics_display)
        
        self.record_indicator.config(text="", fg='#444')
        self.timer_label.config(text="00:00:00")
        self.record_timer.config(text="00:00:00")
        self.recording_status.config(text="● Ожидание", fg=self.color_scheme['text2'])
        self.status_text.config(text="● Авто-запись завершена", fg=self.color_scheme['accent2'])
        self.file_status.config(text="💾 Файл сохранен")
        self.btn_start.config(state='normal', bg='#e94560')
        self.btn_stop.config(state='disabled', bg='#6c757d')

    # ------------------ Ловушки ------------------
    def toggle_photo_trap(self):
        self.photo_trap_enabled = not self.photo_trap_enabled
        if self.photo_trap_enabled:
            self.btn_photo_trap.config(text="📸 ФОТОЛОВУШКА (ВКЛ)", bg='#00b894')
            self.photo_status.config(text="📷 активна", fg=self.color_scheme['accent2'])
            self.prev_gray = None
            self.last_motion_time = 0
            self.motion_counter = 0
            self.consecutive_motion = 0
            self.motion_history = []
            self.status_text.config(text="● Фотоловушка включена", fg=self.color_scheme['accent2'])
        else:
            self.btn_photo_trap.config(text="📸 ФОТОЛОВУШКА", bg='#6c757d')
            self.photo_status.config(text="📷 выкл", fg=self.color_scheme['text3'])
            self.status_text.config(text="● Фотоловушка выключена", fg=self.color_scheme['text3'])

    def toggle_video_trap(self):
        self.video_trap_enabled = not self.video_trap_enabled
        if self.video_trap_enabled:
            self.btn_video_trap.config(text="🎥 ВИДЕОЛОВУШКА (ВКЛ)", bg='#00b894')
            self.video_status.config(text="🎬 активна (5 мин)", fg=self.color_scheme['accent2'])
            self.prev_gray = None
            self.last_motion_time = 0
            self.motion_counter = 0
            self.consecutive_motion = 0
            self.motion_history = []
            self.status_text.config(text="● Видеоловушка включена (5 мин)", fg=self.color_scheme['accent2'])
        else:
            self.btn_video_trap.config(text="🎥 ВИДЕОЛОВУШКА (5 мин)", bg='#6c757d')
            self.video_status.config(text="🎬 выкл", fg=self.color_scheme['text3'])
            self.status_text.config(text="● Видеоловушка выключена", fg=self.color_scheme['text3'])
            if self.motion_recording:
                self.stop_motion_recording()

    # ------------------ Настройки ------------------
    def open_settings(self):
        settings_win = tk.Toplevel(self.root)
        settings_win.title("Настройки")
        settings_win.geometry("600x800")
        settings_win.configure(bg=self.color_scheme['bg'])
        settings_win.transient(self.root)
        settings_win.grab_set()

        tk.Label(settings_win, text="⚙ Настройки", font=('Arial', 16, 'bold'),
                bg=self.color_scheme['bg'], fg='white').pack(pady=15)

        container = tk.Frame(settings_win, bg=self.color_scheme['bg'])
        container.pack(fill=tk.BOTH, expand=True, padx=20)

        # Папка сохранения
        tk.Label(container, text="📁 Папка сохранения:", font=('Arial', 11),
                bg=self.color_scheme['bg'], fg='white').pack(anchor='w', pady=(10, 2))
        folder_frame = tk.Frame(container, bg=self.color_scheme['bg'])
        folder_frame.pack(fill=tk.X, pady=5)
        
        folder_var = tk.StringVar(value=self.output_path)
        folder_entry = tk.Entry(folder_frame, textvariable=folder_var, 
                               font=('Arial', 10), bg=self.color_scheme['bg3'], fg='white',
                               insertbackground='white', relief=tk.FLAT)
        folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        tk.Button(folder_frame, text="Обзор", command=lambda: self.browse_folder(folder_var),
                 bg=self.color_scheme['accent2'], fg='white', font=('Arial', 10), 
                 relief=tk.FLAT, cursor='hand2').pack(side=tk.RIGHT)

        ttk.Separator(container, orient='horizontal').pack(fill=tk.X, pady=10)

        # Звуковые настройки
        sound_frame = tk.LabelFrame(container, text=" 🔊 ЗВУКОВОЕ ОПОВЕЩЕНИЕ ", 
                                    font=('Arial', 11, 'bold'),
                                    bg=self.color_scheme['bg2'], fg='white', 
                                    relief=tk.FLAT)
        sound_frame.pack(fill=tk.X, pady=5)
        
        sound_inner = tk.Frame(sound_frame, bg=self.color_scheme['bg2'])
        sound_inner.pack(pady=8, padx=8)
        
        # Включение/отключение звука
        sound_enabled_var = tk.BooleanVar(value=self.sound_enabled)
        tk.Checkbutton(
            sound_inner,
            text="🔊 Включить звуковое оповещение",
            variable=sound_enabled_var,
            command=lambda: self.toggle_sound(sound_enabled_var),
            bg=self.color_scheme['bg2'],
            fg='white',
            selectcolor=self.color_scheme['bg3'],
            font=('Arial', 10)
        ).pack(anchor='w', pady=3)
        
        # Громкость
        tk.Label(sound_inner, text="Громкость:", font=('Arial', 10),
                bg=self.color_scheme['bg2'], fg='white').pack(anchor='w', pady=2)
        volume_var = tk.DoubleVar(value=self.sound_volume)
        volume_scale = tk.Scale(sound_inner, from_=0.0, to=1.0, resolution=0.1,
                               orient=tk.HORIZONTAL, variable=volume_var,
                               bg=self.color_scheme['bg2'], fg='white',
                               troughcolor=self.color_scheme['bg3'], 
                               sliderlength=20, length=300,
                               command=lambda v: self.set_volume(float(v)))
        volume_scale.pack(pady=3)
        
        # Выбор звукового файла
        tk.Label(sound_inner, text="Звуковой файл:", font=('Arial', 10),
                bg=self.color_scheme['bg2'], fg='white').pack(anchor='w', pady=2)
        
        sound_file_frame = tk.Frame(sound_inner, bg=self.color_scheme['bg2'])
        sound_file_frame.pack(fill=tk.X, pady=3)
        
        sound_file_var = tk.StringVar(value=self.custom_sound_path if self.custom_sound_path else "Стандартный звук")
        sound_file_entry = tk.Entry(sound_file_frame, textvariable=sound_file_var,
                                    font=('Arial', 9), bg=self.color_scheme['bg3'], fg='white',
                                    insertbackground='white', relief=tk.FLAT, state='readonly')
        sound_file_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        tk.Button(sound_file_frame, text="Выбрать файл", 
                 command=lambda: self.select_sound_file(sound_file_var),
                 bg=self.color_scheme['accent2'], fg='white', font=('Arial', 9),
                 relief=tk.FLAT, cursor='hand2').pack(side=tk.RIGHT)
        
        # Кнопка тестового воспроизведения
        tk.Button(sound_inner, text="🔊 Тест звука", 
                 command=self.test_sound,
                 bg='#ffd93d', fg=self.color_scheme['bg'], font=('Arial', 9, 'bold'),
                 relief=tk.FLAT, cursor='hand2').pack(pady=5)

        ttk.Separator(container, orient='horizontal').pack(fill=tk.X, pady=10)

        # Системные настройки
        sys_frame = tk.LabelFrame(container, text=" Система ", 
                                   font=('Arial', 11, 'bold'),
                                   bg=self.color_scheme['bg2'], fg='white', 
                                   relief=tk.FLAT)
        sys_frame.pack(fill=tk.X, pady=5)
        
        sys_inner = tk.Frame(sys_frame, bg=self.color_scheme['bg2'])
        sys_inner.pack(pady=8, padx=8)
        
        auto_start_var = tk.BooleanVar(value=self.auto_start)
        tk.Checkbutton(
            sys_inner,
            text="🚀 Автозапуск при старте системы",
            variable=auto_start_var,
            command=lambda: self.toggle_auto_start(auto_start_var),
            bg=self.color_scheme['bg2'],
            fg='white',
            selectcolor=self.color_scheme['bg3'],
            font=('Arial', 10)
        ).pack(anchor='w', pady=3)
        
        prevent_sleep_var = tk.BooleanVar(value=self.prevent_sleep)
        tk.Checkbutton(
            sys_inner,
            text="💤 Отключить спящий режим",
            variable=prevent_sleep_var,
            command=lambda: self.toggle_prevent_sleep(prevent_sleep_var),
            bg=self.color_scheme['bg2'],
            fg='white',
            selectcolor=self.color_scheme['bg3'],
            font=('Arial', 10)
        ).pack(anchor='w', pady=3)

        ttk.Separator(container, orient='horizontal').pack(fill=tk.X, pady=10)

        # Настройки камеры
        cam_frame = tk.LabelFrame(container, text=" Камера ", 
                                   font=('Arial', 11, 'bold'),
                                   bg=self.color_scheme['bg2'], fg='white', 
                                   relief=tk.FLAT)
        cam_frame.pack(fill=tk.X, pady=5)
        
        cam_inner = tk.Frame(cam_frame, bg=self.color_scheme['bg2'])
        cam_inner.pack(pady=8, padx=8)
        
        tk.Label(cam_inner, text="🎯 Желаемый FPS:", font=('Arial', 10),
                bg=self.color_scheme['bg2'], fg='white').pack(anchor='w', pady=2)
        fps_var = tk.StringVar(value=str(self.target_fps))
        fps_combo = ttk.Combobox(cam_inner, textvariable=fps_var, 
                                values=["15", "30", "60", "120"], state="readonly")
        fps_combo.pack(fill=tk.X, pady=3)

        # Настройки ловушки
        trap_settings_frame = tk.LabelFrame(container, text=" Ловушка ", 
                                           font=('Arial', 11, 'bold'),
                                           bg=self.color_scheme['bg2'], fg='white', 
                                           relief=tk.FLAT)
        trap_settings_frame.pack(fill=tk.X, pady=5)
        
        trap_settings_inner = tk.Frame(trap_settings_frame, bg=self.color_scheme['bg2'])
        trap_settings_inner.pack(pady=8, padx=8)
        
        tk.Label(trap_settings_inner, text="🎯 Чувствительность:", font=('Arial', 10),
                bg=self.color_scheme['bg2'], fg='white').pack(anchor='w', pady=2)
        sens_var = tk.StringVar(value=str(self.motion_threshold))
        sens_scale = tk.Scale(trap_settings_inner, from_=50, to=500, orient=tk.HORIZONTAL,
                             variable=sens_var, bg=self.color_scheme['bg2'], fg='white',
                             troughcolor=self.color_scheme['bg3'], sliderlength=20, length=300)
        sens_scale.pack(pady=3)
        
        tk.Label(trap_settings_inner, text="⏱ Задержка (сек):", font=('Arial', 10),
                bg=self.color_scheme['bg2'], fg='white').pack(anchor='w', pady=2)
        cooldown_var = tk.StringVar(value=str(self.motion_cooldown))
        cooldown_scale = tk.Scale(trap_settings_inner, from_=0.5, to=10, resolution=0.5,
                                 orient=tk.HORIZONTAL, variable=cooldown_var,
                                 bg=self.color_scheme['bg2'], fg='white', 
                                 troughcolor=self.color_scheme['bg3'], sliderlength=20, length=300)
        cooldown_scale.pack(pady=3)
        
        tk.Label(trap_settings_inner, text="⏱ Длительность видео (мин):", font=('Arial', 10),
                bg=self.color_scheme['bg2'], fg='white').pack(anchor='w', pady=2)
        duration_var = tk.StringVar(value=str(self.motion_record_duration // 60))
        duration_scale = tk.Scale(trap_settings_inner, from_=1, to=30, orient=tk.HORIZONTAL,
                                 variable=duration_var, bg=self.color_scheme['bg2'], fg='white',
                                 troughcolor=self.color_scheme['bg3'], sliderlength=20, length=300)
        duration_scale.pack(pady=3)

        # Аудио
        audio_var = tk.BooleanVar(value=self.audio_enabled)
        tk.Checkbutton(
            container,
            text="🎤 Записывать звук",
            variable=audio_var,
            bg=self.color_scheme['bg'],
            fg='white',
            selectcolor=self.color_scheme['bg3'],
            font=('Arial', 10)
        ).pack(anchor='w', pady=5)

        # Кнопки
        btn_frame = tk.Frame(container, bg=self.color_scheme['bg'])
        btn_frame.pack(pady=20)

        tk.Button(btn_frame, text="✅ Применить", 
                 command=lambda: self.apply_settings(settings_win, folder_var, fps_var, 
                                                    sens_var, cooldown_var, duration_var, 
                                                    audio_var, sound_enabled_var, volume_var),
                 bg=self.color_scheme['accent2'], fg='white', font=('Arial', 10, 'bold'),
                 padx=20, pady=8, relief=tk.FLAT, cursor='hand2').pack(side=tk.LEFT, padx=5)

        tk.Button(btn_frame, text="📤 Экспорт", command=self.export_settings,
                 bg='#e94560', fg='white', font=('Arial', 10, 'bold'),
                 padx=20, pady=8, relief=tk.FLAT, cursor='hand2').pack(side=tk.LEFT, padx=5)

        tk.Button(btn_frame, text="📥 Импорт", command=self.import_settings,
                 bg='#ffd93d', fg=self.color_scheme['bg'], font=('Arial', 10, 'bold'),
                 padx=20, pady=8, relief=tk.FLAT, cursor='hand2').pack(side=tk.LEFT, padx=5)

    def toggle_sound(self, var):
        """Включение/отключение звука"""
        self.sound_enabled = var.get()
        if self.sound_enabled:
            self.sound_status.config(text="🔊 звук вкл", fg=self.color_scheme['accent2'])
            self.status_text.config(text="● Звук включен", fg=self.color_scheme['accent2'])
        else:
            self.sound_status.config(text="🔇 звук выкл", fg=self.color_scheme['text3'])
            self.status_text.config(text="● Звук выключен", fg=self.color_scheme['text2'])

    def set_volume(self, volume):
        """Установка громкости"""
        self.sound_volume = volume

    def select_sound_file(self, var):
        """Выбор пользовательского звукового файла"""
        file_path = filedialog.askopenfilename(
            title="Выберите звуковой файл",
            filetypes=[
                ("Звуковые файлы", "*.wav *.mp3 *.ogg"),
                ("WAV файлы", "*.wav"),
                ("MP3 файлы", "*.mp3"),
                ("OGG файлы", "*.ogg"),
                ("Все файлы", "*.*")
            ]
        )
        if file_path:
            self.custom_sound_path = file_path
            var.set(os.path.basename(file_path))
            self.status_text.config(text=f"● Выбран звук: {os.path.basename(file_path)}", 
                                   fg=self.color_scheme['accent2'])
            # Тестируем выбранный звук
            self.test_sound()

    def test_sound(self):
        """Тестовое воспроизведение звука"""
        self.play_sound()

    def toggle_auto_start(self, var):
        self.auto_start = var.get()
        if self.auto_start:
            self.add_to_startup()
        else:
            self.remove_from_startup()

    def remove_from_startup(self):
        try:
            if platform.system() == "Windows":
                import winreg
                key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
                try:
                    winreg.DeleteValue(key, "VideoRecorderPro")
                except:
                    pass
                winreg.CloseKey(key)
            elif platform.system() == "Linux":
                desktop_path = os.path.expanduser("~/.config/autostart/videorecorder.desktop")
                if os.path.exists(desktop_path):
                    os.remove(desktop_path)
            self.status_text.config(text="● Удалено из автозагрузки", fg=self.color_scheme['text2'])
        except Exception as e:
            print(f"Ошибка удаления из автозагрузки: {e}")

    def toggle_prevent_sleep(self, var):
        self.prevent_sleep = var.get()
        if self.prevent_sleep:
            self.disable_sleep_mode()
        else:
            self.enable_sleep_mode()

    def enable_sleep_mode(self):
        try:
            if platform.system() == "Windows":
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            self.status_text.config(text="● Спящий режим включен", fg=self.color_scheme['text2'])
        except Exception as e:
            print(f"Ошибка включения спящего режима: {e}")

    def apply_settings(self, win, folder_var, fps_var, sens_var, cooldown_var, duration_var, 
                       audio_var, sound_enabled_var, volume_var):
        new_folder = folder_var.get().strip()
        if new_folder:
            if not os.path.exists(new_folder):
                os.makedirs(new_folder)
            self.output_path = new_folder
            self.photos_folder = os.path.join(self.output_path, "Photos")
            if not os.path.exists(self.photos_folder):
                os.makedirs(self.photos_folder)
            self.refresh_statistics()

        try:
            new_fps = float(fps_var.get())
            if new_fps > 0:
                self.target_fps = new_fps
                self.root.after(100, self.reconnect_camera)
        except:
            pass

        try:
            val = int(sens_var.get())
            if val >= 50:
                self.motion_threshold = val
        except:
            pass

        try:
            val = float(cooldown_var.get())
            if val >= 0.5:
                self.motion_cooldown = val
        except:
            pass

        try:
            val = int(duration_var.get())
            if val >= 1 and val <= 30:
                self.motion_record_duration = val * 60
        except:
            pass

        self.audio_enabled = audio_var.get()
        
        # Звуковые настройки
        self.sound_enabled = sound_enabled_var.get()
        if self.sound_enabled:
            self.sound_status.config(text="🔊 звук вкл", fg=self.color_scheme['accent2'])
        else:
            self.sound_status.config(text="🔇 звук выкл", fg=self.color_scheme['text3'])
        
        self.sound_volume = volume_var.get()
        
        win.destroy()
        self.status_text.config(text="● Настройки применены", fg=self.color_scheme['accent2'])

    def export_settings(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Экспорт настроек"
        )
        if not file_path:
            return
        data = {
            "output_path": self.output_path,
            "target_fps": self.target_fps,
            "motion_threshold": self.motion_threshold,
            "motion_cooldown": self.motion_cooldown,
            "motion_record_duration": self.motion_record_duration,
            "audio_enabled": self.audio_enabled,
            "auto_start": self.auto_start,
            "prevent_sleep": self.prevent_sleep,
            "sound_enabled": self.sound_enabled,
            "sound_volume": self.sound_volume,
            "custom_sound_path": self.custom_sound_path
        }
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            messagebox.showinfo("Экспорт", f"Настройки сохранены в:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить настройки:\n{str(e)}")

    def import_settings(self):
        file_path = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Импорт настроек"
        )
        if not file_path:
            return
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if "output_path" in data and data["output_path"]:
                self.output_path = data["output_path"]
                self.photos_folder = os.path.join(self.output_path, "Photos")
                if not os.path.exists(self.photos_folder):
                    os.makedirs(self.photos_folder)
                self.refresh_statistics()
            if "target_fps" in data and data["target_fps"] > 0:
                self.target_fps = data["target_fps"]
                self.root.after(100, self.reconnect_camera)
            if "motion_threshold" in data and data["motion_threshold"] >= 50:
                self.motion_threshold = data["motion_threshold"]
            if "motion_cooldown" in data and data["motion_cooldown"] >= 0.5:
                self.motion_cooldown = data["motion_cooldown"]
            if "motion_record_duration" in data and data["motion_record_duration"] >= 60:
                self.motion_record_duration = data["motion_record_duration"]
            if "audio_enabled" in data:
                self.audio_enabled = data["audio_enabled"]
            if "auto_start" in data:
                self.auto_start = data["auto_start"]
            if "prevent_sleep" in data:
                self.prevent_sleep = data["prevent_sleep"]
            if "sound_enabled" in data:
                self.sound_enabled = data["sound_enabled"]
                if self.sound_enabled:
                    self.sound_status.config(text="🔊 звук вкл", fg=self.color_scheme['accent2'])
                else:
                    self.sound_status.config(text="🔇 звук выкл", fg=self.color_scheme['text3'])
            if "sound_volume" in data:
                self.sound_volume = data["sound_volume"]
            if "custom_sound_path" in data and data["custom_sound_path"]:
                if os.path.exists(data["custom_sound_path"]):
                    self.custom_sound_path = data["custom_sound_path"]
            messagebox.showinfo("Импорт", "Настройки успешно загружены и применены.")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить настройки:\n{str(e)}")

    def browse_folder(self, var):
        folder = filedialog.askdirectory(title="Выберите папку", initialdir=var.get())
        if folder:
            var.set(folder)

    def reconnect_camera(self):
        self.capture_running = False
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        time.sleep(0.5)
        self.connect_camera(try_indices=[self.camera_index, 0, 1, 2])

    # ------------------ Закрытие ------------------
    def on_close(self):
        self.capture_running = False
        if self.is_recording or self.motion_recording:
            self.stop_recording()
        if self.cap is not None:
            self.cap.release()
        # Очищаем временный звуковой файл
        if self.default_sound_path and os.path.exists(self.default_sound_path):
            try:
                os.remove(self.default_sound_path)
            except:
                pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = VideoRecorderApp(root)
    root.mainloop()