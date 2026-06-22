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

class VideoRecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Видеорегистратор + Фото/Видеоловушка")
        self.root.geometry("900x800")
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

        self.display_width = 720
        self.display_height = 0
        self.last_frame = None
        self.frame_lock = threading.Lock()

        # ---------- Состояния записи ----------
        self.is_recording = False
        self.video_writer = None
        self.motion_recording = False
        self.motion_timer_id = None

        # ---------- Параметры ловушек ----------
        self.photo_trap_enabled = False
        self.video_trap_enabled = False
        self.prev_gray = None
        self.motion_threshold = 100
        self.motion_cooldown = 1.0
        self.last_motion_time = 0
        self.photos_folder = os.path.join(self.output_path, "Photos")
        if not os.path.exists(self.photos_folder):
            os.makedirs(self.photos_folder)

        self.capture_running = True

        self.create_widgets()
        self.connect_camera()

    # ------------------ Интерфейс ------------------
    def create_widgets(self):
        self.video_frame = tk.Frame(self.root, bg="black")
        self.video_frame.pack(pady=10)

        self.video_label = tk.Label(self.video_frame, bg="black")
        self.video_label.pack()

        control_frame1 = tk.Frame(self.root)
        control_frame1.pack(pady=5)

        self.btn_start = tk.Button(control_frame1, text="Начать запись", command=self.start_recording, width=15)
        self.btn_start.grid(row=0, column=0, padx=5)

        self.btn_stop = tk.Button(control_frame1, text="Остановить запись", command=self.stop_recording, state=tk.DISABLED, width=15)
        self.btn_stop.grid(row=0, column=1, padx=5)

        self.btn_settings = tk.Button(control_frame1, text="Настройки", command=self.open_settings, width=15)
        self.btn_settings.grid(row=0, column=2, padx=5)

        control_frame2 = tk.Frame(self.root)
        control_frame2.pack(pady=5)

        self.btn_photo_trap = tk.Button(control_frame2, text="Фотоловушка (выкл)", command=self.toggle_photo_trap, width=20, bg="SystemButtonFace")
        self.btn_photo_trap.grid(row=0, column=0, padx=10)

        self.btn_video_trap = tk.Button(control_frame2, text="Видеоловушка (выкл)", command=self.toggle_video_trap, width=20, bg="SystemButtonFace")
        self.btn_video_trap.grid(row=0, column=1, padx=10)

        self.status_label = tk.Label(self.root, text="Статус: Ожидание", font=("Arial", 10))
        self.status_label.pack(pady=5)

        self.file_info_label = tk.Label(self.root, text="Файл: не выбран", font=("Arial", 9))
        self.file_info_label.pack()

        self.photo_status_label = tk.Label(self.root, text="Фото: выключена", font=("Arial", 9), fg="gray")
        self.photo_status_label.pack()

        self.video_trap_status_label = tk.Label(self.root, text="Видео: выключена", font=("Arial", 9), fg="gray")
        self.video_trap_status_label.pack()

        self.rec_type_label = tk.Label(self.root, text="Тип записи: нет", font=("Arial", 9), fg="blue")
        self.rec_type_label.pack()

        # ▼▼▼ УДАЛЕНА СТРОКА СО СТАТУСОМ КАМЕРЫ ▼▼▼
        # self.camera_info_label = tk.Label(...)

    # ---------- Наложение даты/времени на кадр ----------
    def add_timestamp_to_frame(self, frame):
        """Добавляет на кадр текущую дату и время."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, timestamp, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                    1, (0, 255, 255), 2, cv2.LINE_AA)
        return frame

    # ------------------ Камера и измерение FPS ------------------
    def measure_real_fps(self, cap, num_frames=30):
        start_time = time.time()
        for _ in range(num_frames):
            ret, frame = cap.read()
            if not ret or frame is None:
                break
        elapsed = time.time() - start_time
        if elapsed > 0:
            return num_frames / elapsed
        return 30.0

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
                    # ▼▼▼ НЕ ОБНОВЛЯЕМ НАДПИСЬ ▼▼▼

                    self.display_height = int(self.display_width * self.height / self.width)

                    self.capture_running = True
                    self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
                    self.capture_thread.start()
                    self.update_display()
                    return True
                else:
                    cap.release()
            else:
                if api_preference == cv2.CAP_DSHOW:
                    cap2 = cv2.VideoCapture(idx)
                    if cap2.isOpened():
                        ret, frame = cap2.read()
                        if ret and frame is not None:
                            self.width, self.height = self.get_max_resolution(cap2)
                            cap2.set(cv2.CAP_PROP_FPS, self.target_fps)
                            real_fps = self.measure_real_fps(cap2, 30)
                            self.actual_fps = real_fps

                            self.cap = cap2
                            self.camera_index = idx
                            # ▼▼▼ НЕ ОБНОВЛЯЕМ НАДПИСЬ ▼▼▼

                            self.display_height = int(self.display_width * self.height / self.width)

                            self.capture_running = True
                            self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
                            self.capture_thread.start()
                            self.update_display()
                            return True
                        else:
                            cap2.release()

        # ▼▼▼ НЕ ВЫВОДИМ СООБЩЕНИЕ ОБ ОШИБКЕ В ИНТЕРФЕЙС ▼▼▼
        if not hasattr(self, '_error_shown'):
            self._error_shown = True
            messagebox.showerror("Ошибка", 
                "Не удалось открыть камеру ни по одному из индексов (0,1,2).\n"
                "Проверьте, что камера не занята другим приложением.")
        return False

    def get_max_resolution(self, cap):
        resolutions = [
            (3840, 2160), (2560, 1440), (1920, 1080),
            (1280, 720), (1024, 768), (800, 600), (640, 480)
        ]
        for w, h in resolutions:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if abs(aw - w) < 10 and abs(ah - h) < 10:
                return aw, ah
        return int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    def capture_loop(self):
        """Поток захвата – добавляет дату/время, записывает и сохраняет для отображения."""
        while self.capture_running and self.cap is not None and self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret or frame is None:
                time.sleep(0.01)
                continue

            # ---- НАКЛАДЫВАЕМ ДАТУ И ВРЕМЯ ----
            frame_with_time = self.add_timestamp_to_frame(frame)

            # Запись видео (если активна)
            if self.video_writer is not None:
                self.video_writer.write(frame_with_time)

            # Детекция движения (используем кадр с датой для единообразия)
            if self.photo_trap_enabled or self.video_trap_enabled:
                self.detect_motion(frame_with_time)

            # Сохраняем для отображения
            with self.frame_lock:
                self.last_frame = frame_with_time.copy()

            time.sleep(0.001)

    def update_display(self):
        """Обновляет изображение на экране."""
        if self.last_frame is not None:
            with self.frame_lock:
                frame = self.last_frame.copy()
            display_frame = cv2.resize(frame, (self.display_width, self.display_height), interpolation=cv2.INTER_LANCZOS4)
            frame_rgb = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
            imgtk = ImageTk.PhotoImage(image=img)
            self.video_label.imgtk = imgtk
            self.video_label.config(image=imgtk)

        self.root.after(30, self.update_display)

    # ------------------ Детекция движения (в потоке) ------------------
    def detect_motion(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self.prev_gray is None:
            self.prev_gray = gray
            return

        diff = cv2.absdiff(self.prev_gray, gray)
        thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)[1]
        changed_pixels = cv2.countNonZero(thresh)

        if changed_pixels > self.motion_threshold:
            current_time = time.time()
            if current_time - self.last_motion_time >= self.motion_cooldown:
                self.last_motion_time = current_time

                if self.photo_trap_enabled:
                    self.save_photo(frame)   # кадр уже с датой
                    self.root.after(0, lambda: self.photo_status_label.config(text="Фото: движение! сохранено", fg="red"))
                    self.root.after(1000, lambda: self.photo_status_label.config(
                        text="Фото: активна" if self.photo_trap_enabled else "Фото: выключена",
                        fg="green" if self.photo_trap_enabled else "gray"
                    ))

                if self.video_trap_enabled and not self.is_recording and not self.motion_recording:
                    self.root.after(0, self.start_motion_recording)

        self.prev_gray = gray

    def save_photo(self, frame):
        """Сохраняет фото с уже наложенной датой/временем."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"motion_photo_{timestamp}.jpg"
        filepath = os.path.join(self.photos_folder, filename)
        cv2.imwrite(filepath, frame)
        self.file_info_label.config(text=f"Фото сохранено: {filename}")

    # ------------------ Запись (ручная и авто) ------------------
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
        self.rec_type_label.config(text="Тип записи: РУЧНАЯ", fg="green")
        self.status_label.config(text="Статус: Идет ручная запись...")
        self.file_info_label.config(text=f"Файл: {filepath}")
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_settings.config(state=tk.DISABLED)

    def stop_recording(self):
        if self.is_recording:
            self.is_recording = False
            if self.video_writer is not None:
                self.video_writer.release()
                self.video_writer = None
            self.rec_type_label.config(text="Тип записи: нет", fg="blue")
            self.status_label.config(text="Статус: Ручная запись остановлена")
            self.file_info_label.config(text="Файл: сохранен")
            self.btn_start.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.DISABLED)
            self.btn_settings.config(state=tk.NORMAL)
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
        self.rec_type_label.config(text="Тип записи: АВТО (видеоловушка)", fg="orange")
        self.motion_timer_id = self.root.after(15 * 60 * 1000, self.stop_motion_recording)
        self.status_label.config(text="Статус: Авто-запись (15 мин)")
        self.file_info_label.config(text=f"Файл: {filepath}")
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_settings.config(state=tk.DISABLED)

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
        self.rec_type_label.config(text="Тип записи: нет", fg="blue")
        self.status_label.config(text="Статус: Авто-запись завершена")
        self.file_info_label.config(text="Файл: сохранен")
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.btn_settings.config(state=tk.NORMAL)

    # ------------------ Ловушки ------------------
    def toggle_photo_trap(self):
        self.photo_trap_enabled = not self.photo_trap_enabled
        if self.photo_trap_enabled:
            self.btn_photo_trap.config(text="Фотоловушка (вкл)", bg="lightgreen")
            self.photo_status_label.config(text="Фото: активна", fg="green")
            self.prev_gray = None
            self.last_motion_time = 0
        else:
            self.btn_photo_trap.config(text="Фотоловушка (выкл)", bg="SystemButtonFace")
            self.photo_status_label.config(text="Фото: выключена", fg="gray")

    def toggle_video_trap(self):
        self.video_trap_enabled = not self.video_trap_enabled
        if self.video_trap_enabled:
            self.btn_video_trap.config(text="Видеоловушка (вкл)", bg="lightblue")
            self.video_trap_status_label.config(text="Видео: активна", fg="green")
            self.prev_gray = None
            self.last_motion_time = 0
        else:
            self.btn_video_trap.config(text="Видеоловушка (выкл)", bg="SystemButtonFace")
            self.video_trap_status_label.config(text="Видео: выключена", fg="gray")
            if self.motion_recording:
                self.stop_motion_recording()

    # ------------------ Настройки (без индекса камеры) ------------------
    def open_settings(self):
        settings_win = tk.Toplevel(self.root)
        settings_win.title("Настройки")
        settings_win.geometry("400x450")
        settings_win.transient(self.root)
        settings_win.grab_set()

        tk.Label(settings_win, text="Папка сохранения:").pack(pady=5)
        folder_var = tk.StringVar(value=self.output_path)
        tk.Entry(settings_win, textvariable=folder_var, width=40).pack(pady=5)
        tk.Button(settings_win, text="Обзор...", command=lambda: self.browse_folder(folder_var)).pack(pady=2)

        tk.Label(settings_win, text="Желаемый FPS (для отображения):").pack(pady=5)
        fps_var = tk.StringVar(value=str(self.target_fps))
        fps_combo = ttk.Combobox(settings_win, textvariable=fps_var, values=["15", "30", "60", "120"], state="readonly")
        fps_combo.pack(pady=5)

        # Поле "Индекс камеры" УДАЛЕНО

        tk.Label(settings_win, text="Чувствительность (пикселей):").pack(pady=5)
        sens_var = tk.StringVar(value=str(self.motion_threshold))
        tk.Entry(settings_win, textvariable=sens_var, width=10).pack(pady=5)

        tk.Label(settings_win, text="Задержка между срабатываниями (сек):").pack(pady=5)
        cooldown_var = tk.StringVar(value=str(self.motion_cooldown))
        tk.Entry(settings_win, textvariable=cooldown_var, width=10).pack(pady=5)

        def apply_settings():
            new_folder = folder_var.get().strip()
            if new_folder:
                if not os.path.exists(new_folder):
                    os.makedirs(new_folder)
                self.output_path = new_folder
                self.photos_folder = os.path.join(self.output_path, "Photos")
                if not os.path.exists(self.photos_folder):
                    os.makedirs(self.photos_folder)

            try:
                new_fps = float(fps_var.get())
                if new_fps > 0:
                    self.target_fps = new_fps
                    self.root.after(100, self.reconnect_camera)
            except ValueError:
                pass

            # Блок считывания camera_index УДАЛЁН

            try:
                val = int(sens_var.get())
                if val > 0:
                    self.motion_threshold = val
            except ValueError:
                pass

            try:
                val = float(cooldown_var.get())
                if val >= 0.1:
                    self.motion_cooldown = val
            except ValueError:
                pass

            settings_win.destroy()
            messagebox.showinfo("Настройки", "Настройки применены. Камера переподключится для замера FPS.")

        def export_settings():
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
                # camera_index исключён
                "motion_threshold": self.motion_threshold,
                "motion_cooldown": self.motion_cooldown
            }
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                messagebox.showinfo("Экспорт", f"Настройки сохранены в:\n{file_path}")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить настройки:\n{str(e)}")

        def import_settings():
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
                    folder_var.set(self.output_path)
                if "target_fps" in data and data["target_fps"] > 0:
                    self.target_fps = data["target_fps"]
                    fps_var.set(str(self.target_fps))
                    self.root.after(100, self.reconnect_camera)
                # camera_index игнорируется
                if "motion_threshold" in data and data["motion_threshold"] > 0:
                    self.motion_threshold = data["motion_threshold"]
                    sens_var.set(str(self.motion_threshold))
                if "motion_cooldown" in data and data["motion_cooldown"] >= 0.1:
                    self.motion_cooldown = data["motion_cooldown"]
                    cooldown_var.set(str(self.motion_cooldown))
                messagebox.showinfo("Импорт", "Настройки успешно загружены и применены.")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить настройки:\n{str(e)}")

        btn_frame = tk.Frame(settings_win)
        btn_frame.pack(pady=15)

        tk.Button(btn_frame, text="Применить", command=apply_settings, width=12).grid(row=0, column=0, padx=5)
        tk.Button(btn_frame, text="Экспорт настроек", command=export_settings, width=15).grid(row=0, column=1, padx=5)
        tk.Button(btn_frame, text="Импорт настроек", command=import_settings, width=15).grid(row=0, column=2, padx=5)

    def browse_folder(self, var):
        folder = filedialog.askdirectory(title="Выберите папку", initialdir=var.get())
        if folder:
            var.set(folder)

    def reconnect_camera(self):
        self.capture_running = False
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.connect_camera(try_indices=[self.camera_index, 0, 1, 2])

    # ------------------ Закрытие ------------------
    def on_close(self):
        self.capture_running = False
        if self.is_recording or self.motion_recording:
            self.stop_recording()
        if self.cap is not None:
            self.cap.release()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = VideoRecorderApp(root)
    root.mainloop()