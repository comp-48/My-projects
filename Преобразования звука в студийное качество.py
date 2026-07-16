import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import os
import numpy as np
from scipy import signal
from pydub import AudioSegment

class AutoStudioProcessor:
    def __init__(self, root):
        self.root = root
        self.root.title("Автоматический студийный процессор")
        self.root.geometry("550x200")
        self.root.resizable(False, False)

        # Переменные для путей
        self.input_path = tk.StringVar()
        self.output_path = tk.StringVar()

        # Создаём интерфейс
        self.create_widgets()

    def create_widgets(self):
        # Заголовок
        tk.Label(self.root, text="Преобразование звука в студийное качество", 
                 font=("Arial", 14)).pack(pady=10)

        # Фрейм для выбора файлов
        file_frame = tk.LabelFrame(self.root, text="Файлы", padx=10, pady=10)
        file_frame.pack(fill="x", padx=10, pady=5)

        tk.Label(file_frame, text="Входной файл:").grid(row=0, column=0, sticky="w")
        tk.Entry(file_frame, textvariable=self.input_path, width=45).grid(row=0, column=1, padx=5)
        tk.Button(file_frame, text="Обзор", command=self.select_input).grid(row=0, column=2)

        tk.Label(file_frame, text="Выходной файл:").grid(row=1, column=0, sticky="w")
        tk.Entry(file_frame, textvariable=self.output_path, width=45).grid(row=1, column=1, padx=5)
        tk.Button(file_frame, text="Обзор", command=self.select_output).grid(row=1, column=2)

        # Кнопка обработки
        self.process_btn = tk.Button(self.root, text="Преобразовать", 
                                     command=self.start_processing,
                                     bg="lightgreen", font=("Arial", 12), width=20)
        self.process_btn.pack(pady=15)

        # Прогресс-бар и статус
        self.progress = ttk.Progressbar(self.root, length=400, mode='indeterminate')
        self.progress.pack(pady=5)

        self.status_label = tk.Label(self.root, text="Готов к работе", font=("Arial", 10))
        self.status_label.pack(pady=5)

    def select_input(self):
        path = filedialog.askopenfilename(filetypes=[("Аудиофайлы", "*.wav *.mp3 *.flac *.ogg")])
        if path:
            self.input_path.set(path)
            # Автоматически предлагаем выходной файл
            base, ext = os.path.splitext(path)
            self.output_path.set(base + "_studio.wav")

    def select_output(self):
        path = filedialog.asksaveasfilename(defaultextension=".wav",
                                            filetypes=[("WAV files", "*.wav")])
        if path:
            self.output_path.set(path)

    def start_processing(self):
        if not self.input_path.get():
            messagebox.showerror("Ошибка", "Выберите входной файл!")
            return
        if not self.output_path.get():
            messagebox.showerror("Ошибка", "Укажите выходной путь!")
            return

        self.process_btn.config(state="disabled")
        self.progress.start()
        self.status_label.config(text="Обработка...")

        # Запускаем обработку в отдельном потоке
        threading.Thread(target=self.process_audio, daemon=True).start()

    def process_audio(self):
        try:
            # Загружаем аудио
            audio = AudioSegment.from_file(self.input_path.get())
            samples = np.array(audio.get_array_of_samples())
            channels = audio.channels
            sample_rate = audio.frame_rate
            sample_width = audio.sample_width

            # Преобразуем в float32
            if channels > 1:
                samples = samples.reshape((-1, channels))
                processed = np.zeros_like(samples, dtype=np.float32)
                for ch in range(channels):
                    processed[:, ch] = self.apply_fixed_effects(samples[:, ch], sample_rate)
                # Снова в int16
                processed = processed.reshape(-1).astype(np.int16)
            else:
                processed = self.apply_fixed_effects(samples, sample_rate).astype(np.int16)

            # Создаём новый AudioSegment и сохраняем
            processed_audio = AudioSegment(
                data=processed.tobytes(),
                sample_width=sample_width,
                frame_rate=sample_rate,
                channels=channels
            )
            processed_audio.export(self.output_path.get(), format="wav")

            self.root.after(0, lambda: self.status_label.config(text="Готово! Файл сохранён."))
            self.root.after(0, lambda: messagebox.showinfo("Успех", "Преобразование завершено!"))

        except Exception as e:
            self.root.after(0, lambda: self.status_label.config(text="Ошибка: " + str(e)))
            self.root.after(0, lambda: messagebox.showerror("Ошибка", str(e)))
        finally:
            self.root.after(0, lambda: self.progress.stop())
            self.root.after(0, lambda: self.process_btn.config(state="normal"))

    def apply_fixed_effects(self, samples, sample_rate):
        """
        Автоматические эффекты с заранее подобранными параметрами.
        Возвращает обработанный массив int16.
        """
        # Нормализация к [-1, 1]
        x = samples.astype(np.float32) / 32768.0

        # 1. Фильтры (убираем инфра- и ультразвук)
        b_high, a_high = signal.butter(4, 20 / (sample_rate/2), btype='high')
        x = signal.lfilter(b_high, a_high, x)
        b_low, a_low = signal.butter(4, 20000 / (sample_rate/2), btype='low')
        x = signal.lfilter(b_low, a_low, x)

        # 2. Эквалайзер (простой подъём низких и высоких)
        # Низкие (< 250 Гц) +2 дБ
        b_low_eq, a_low_eq = signal.butter(2, 250/(sample_rate/2), btype='low')
        low_signal = signal.lfilter(b_low_eq, a_low_eq, x)
        x = x + (10**(2/20) - 1) * low_signal

        # Высокие (> 4000 Гц) +2 дБ
        b_high_eq, a_high_eq = signal.butter(2, 4000/(sample_rate/2), btype='high')
        high_signal = signal.lfilter(b_high_eq, a_high_eq, x)
        x = x + (10**(2/20) - 1) * high_signal

        # 3. Компрессия (порог -12 дБ, коэффициент 3, атака/релиз упрощённые)
        threshold = 10**(-12/20)
        ratio = 3.0
        # Простая компрессия без сглаживания (для скорости)
        for i in range(len(x)):
            if abs(x[i]) > threshold:
                over = abs(x[i]) / threshold
                gain_reduction = min(1, (over ** (1/ratio)) / over)
                x[i] = x[i] * gain_reduction

        # 4. Общее усиление (+2 дБ)
        x = x * 10**(2/20)

        # 5. Пиковая нормализация до 0.95
        max_abs = np.max(np.abs(x))
        if max_abs > 0:
            x = x / max_abs * 0.95

        # Преобразуем обратно в int16
        x = np.clip(x, -1, 1)
        return (x * 32767).astype(np.int16)

if __name__ == "__main__":
    root = tk.Tk()
    app = AutoStudioProcessor(root)
    root.mainloop()