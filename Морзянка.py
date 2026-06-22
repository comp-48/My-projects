import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import os
import wave
import struct
import math

# ---------- Таблица Морзе ----------
MORSE_TABLE = {
    'A': '.-',    'B': '-...',  'C': '-.-.',  'D': '-..',   'E': '.',
    'F': '..-.',  'G': '--.',   'H': '....',  'I': '..',    'J': '.---',
    'K': '-.-',   'L': '.-..',  'M': '--',    'N': '-.',    'O': '---',
    'P': '.--.',  'Q': '--.-',  'R': '.-.',   'S': '...',   'T': '-',
    'U': '..-',   'V': '...-',  'W': '.--',   'X': '-..-',  'Y': '-.--',
    'Z': '--..',
    'А': '.-',    'Б': '-...',  'В': '.--',   'Г': '--.',   'Д': '-..',
    'Е': '.',     'Ж': '...-',  'З': '--..',  'И': '..',    'Й': '.---',
    'К': '-.-',   'Л': '.-..',  'М': '--',    'Н': '-.',    'О': '---',
    'П': '.--.',  'Р': '.-.',   'С': '...',   'Т': '-',     'У': '..-',
    'Ф': '..-.',  'Х': '....',  'Ц': '-.-.',  'Ч': '---.',  'Ш': '----',
    'Щ': '--.-',  'Ъ': '--.--', 'Ы': '-.--',  'Ь': '-..-',  'Э': '..-..',
    'Ю': '..--',  'Я': '.-.-',
    '0': '-----', '1': '.----', '2': '..---', '3': '...--', '4': '....-',
    '5': '.....', '6': '-....', '7': '--...', '8': '---..', '9': '----.',
    '.': '.-.-.-', ',': '--..--', '?': '..--..', '!': '-.-.--',
    '/': '-..-.',  '(': '-.--.',  ')': '-.--.-', '&': '.-...',
    ':': '---...', ';': '-.-.-.', '=': '-...-',  '+': '.-.-.',
    '-': '-....-', '_': '..--.-', '"': '.-..-.', '$': '...-..-',
    '@': '.--.-.', ' ': '/'  
}
MORSE_TO_CHAR = {v: k for k, v in MORSE_TABLE.items() if v != '/'}
MORSE_TO_CHAR['/'] = ' '

# ---------- Звуковые библиотеки ----------
try:
    import winsound
    def system_beep(freq, dur):
        winsound.Beep(freq, dur)
except ImportError:
    def system_beep(freq, dur):
        pass

# ---------- Функции преобразования ----------
def text_to_morse(text):
    result = []
    for ch in text.upper():
        result.append(MORSE_TABLE.get(ch, '?'))
    return ' '.join(result)

def morse_to_text(morse):
    cleaned = morse.replace('   ', ' / ').replace('/', ' / ')
    words = cleaned.split(' / ')
    out = []
    for word in words:
        chars = word.strip().split()
        for ch in chars:
            out.append(MORSE_TO_CHAR.get(ch, '?'))
        out.append(' ')
    return ''.join(out).strip()

# ---------- Генерация WAV ----------
def generate_morse_wav(morse_str, filename, freq=800, volume=0.5,
                       sample_rate=44100, dot_sec=0.1):
    dash_sec = 3 * dot_sec
    unit = dot_sec
    play_str = morse_str.replace('/', ' / ').replace('   ', ' / ')
    words = play_str.split(' / ')
    actions = []
    for wi, word in enumerate(words):
        symbols = word.split()
        for si, sym in enumerate(symbols):
            for ei, elem in enumerate(sym):
                if elem == '.':
                    actions.append((True, dot_sec))
                elif elem == '-':
                    actions.append((True, dash_sec))
                if ei < len(sym) - 1:
                    actions.append((False, unit))
            if si < len(symbols) - 1:
                actions.append((False, 3 * unit))
        if wi < len(words) - 1:
            actions.append((False, 7 * unit))
    total_samples = int(sample_rate * sum(d for _, d in actions))
    if total_samples == 0:
        return
    max_amp = 32767 * volume
    data = []
    t = 0.0
    for is_sig, dur in actions:
        n = int(sample_rate * dur)
        if is_sig:
            for i in range(n):
                data.append(int(max_amp * math.sin(2 * math.pi * freq * t)))
                t += 1 / sample_rate
        else:
            data.extend([0] * n)
            t += dur
    with wave.open(filename, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack('<' + 'h' * len(data), *data))

# ---------- Распознавание Морзе из аудио ----------
def load_audio_mono(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.wav':
        with wave.open(filepath, 'r') as wf:
            sample_rate = wf.getframerate()
            n_channels = wf.getnchannels()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
        if n_channels == 1:
            fmt = '<{}h'.format(n_frames)
        else:
            fmt = '<{}h'.format(n_frames * n_channels)
        samples = struct.unpack(fmt, raw)
        max_val = 32767
        if n_channels == 1:
            mono = [s / max_val for s in samples]
        else:
            mono = []
            for i in range(0, len(samples), n_channels):
                ch_sum = sum(samples[i+j] for j in range(n_channels))
                mono.append(ch_sum / n_channels / max_val)
        return sample_rate, mono
    else:
        try:
            from pydub import AudioSegment
        except ImportError:
            raise RuntimeError("Для загрузки MP3/OGG необходим pydub: pip install pydub")
        audio = AudioSegment.from_file(filepath)
        sample_rate = audio.frame_rate
        if audio.channels > 1:
            audio = audio.set_channels(1)
        raw = audio.raw_data
        n = len(raw) // 2
        fmt = '<{}h'.format(n)
        samples = struct.unpack(fmt, raw)
        max_val = 32767
        mono = [s / max_val for s in samples]
        return sample_rate, mono

def smooth_energy(samples, window_ms=5, sample_rate=44100):
    window_size = int(window_ms * sample_rate / 1000)
    if window_size < 1:
        window_size = 1
    energy = [s**2 for s in samples]
    smoothed = []
    for i in range(len(energy)):
        start = max(0, i - window_size // 2)
        end = min(len(energy), i + window_size // 2)
        smoothed.append(sum(energy[start:end]) / (end - start))
    return smoothed

def recognize_morse_from_audio(filepath):
    try:
        sr, mono = load_audio_mono(filepath)
    except Exception as e:
        raise RuntimeError(f"Ошибка загрузки аудио: {e}")

    if len(mono) == 0:
        raise RuntimeError("Аудиофайл пуст")

    energy = smooth_energy(mono, window_ms=5, sample_rate=sr)

    max_energy = max(energy)
    if max_energy == 0:
        raise RuntimeError("Аудиофайл не содержит сигнала")

    threshold = 0.3 * max_energy

    intervals = []
    current_type = 'sig' if energy[0] >= threshold else 'pause'
    start_time = 0.0
    time_per_sample = 1.0 / sr

    for i in range(1, len(energy)):
        is_signal = energy[i] >= threshold
        new_type = 'sig' if is_signal else 'pause'
        if new_type != current_type:
            end_time = i * time_per_sample
            duration = end_time - start_time
            if duration > 0:
                intervals.append((duration, current_type))
            start_time = end_time
            current_type = new_type
    end_time = len(energy) * time_per_sample
    duration = end_time - start_time
    if duration > 0:
        intervals.append((duration, current_type))

    min_duration = 0.01
    filtered = [(d, t) for (d, t) in intervals if d >= min_duration]

    if not filtered:
        raise RuntimeError("Не найдено значимых сигналов")

    sig_durations = [d for d, t in filtered if t == 'sig']
    if not sig_durations:
        raise RuntimeError("Нет звуковых сигналов")
    sig_durations.sort()
    median_sig = sig_durations[len(sig_durations)//2]
    unit = median_sig
    if unit <= 0:
        unit = 0.1

    morse_result = []
    current_char_symbols = []
    for dur, typ in filtered:
        if typ == 'sig':
            if dur <= 2 * unit:
                current_char_symbols.append('.')
            else:
                current_char_symbols.append('-')
        else:
            if dur >= 5 * unit:
                if current_char_symbols:
                    morse_result.append(''.join(current_char_symbols))
                    current_char_symbols = []
                morse_result.append('/')
            elif dur >= 2 * unit:
                if current_char_symbols:
                    morse_result.append(''.join(current_char_symbols))
                    current_char_symbols = []
    if current_char_symbols:
        morse_result.append(''.join(current_char_symbols))

    while morse_result and morse_result[0] == '/':
        morse_result.pop(0)
    while morse_result and morse_result[-1] == '/':
        morse_result.pop()

    morse_str = ' '.join(morse_result)
    text = morse_to_text(morse_str.replace('/', ' / '))
    return morse_str, text

# ---------- Поток проигрывания (с частотой) ----------
class PlayerThread(threading.Thread):
    def __init__(self, morse_str, freq=800, callback=None):  # НОВОЕ: параметр freq
        super().__init__(daemon=True)
        self.morse = morse_str
        self.freq = freq          # НОВОЕ
        self.callback = callback
        self.stop_event = threading.Event()

    def run(self):
        # freq теперь берется из self.freq
        freq = self.freq
        unit = 0.1
        dot_sec = unit
        dash_sec = 3 * unit
        play_str = self.morse.replace('/', ' / ').replace('   ', ' / ')
        words = play_str.split(' / ')
        try:
            for wi, word in enumerate(words):
                if self.stop_event.is_set():
                    break
                symbols = word.split()
                for si, sym in enumerate(symbols):
                    if self.stop_event.is_set():
                        break
                    for ei, elem in enumerate(sym):
                        if self.stop_event.is_set():
                            break
                        if elem == '.':
                            system_beep(freq, int(dot_sec * 1000))
                        elif elem == '-':
                            system_beep(freq, int(dash_sec * 1000))
                        time.sleep(unit)
                    if si < len(symbols) - 1:
                        time.sleep(2 * unit)
                if wi < len(words) - 1:
                    time.sleep(6 * unit)
        finally:
            if self.callback:
                self.callback()

# ---------- Графический интерфейс ----------
class MorseApp:
    def __init__(self, master):
        self.master = master
        master.title("Морзянка Pro")
        master.resizable(True, True)

        self.playing = False
        self.live_mode = False
        self.live_after_id = None
        self.current_player = None

        # --- Переменная для частоты (НОВОЕ) ---
        self.freq_var = tk.IntVar(value=800)

        # --- Меню (Правка) ---
        menubar = tk.Menu(master)
        master.config(menu=menubar)
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Правка", menu=edit_menu)
        edit_menu.add_command(label="Копировать текст", command=self.copy_text)
        edit_menu.add_command(label="Копировать код Морзе", command=self.copy_morse)
        edit_menu.add_separator()
        edit_menu.add_command(label="Вставить текст", command=self.paste_text)
        edit_menu.add_command(label="Вставить код Морзе", command=self.paste_morse)

        # --- Фрейм ввода текста ---
        frame_text = ttk.LabelFrame(master, text="Текст (русский, английский, цифры)")
        frame_text.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)

        self.text_input = tk.Text(frame_text, height=5, width=60, font=("Arial", 10))
        self.text_input.pack(padx=5, pady=5, fill=tk.BOTH, expand=True)
        self.text_input.bind("<KeyRelease>", self.on_text_change)

        text_btn_frame = tk.Frame(frame_text)
        text_btn_frame.pack(padx=5, pady=2, fill=tk.X)
        btn_encode = ttk.Button(text_btn_frame, text="→ Кодировать", command=self.encode)
        btn_encode.pack(side=tk.LEFT, padx=2)
        ttk.Button(text_btn_frame, text="📋 Копировать", command=self.copy_text).pack(side=tk.LEFT, padx=2)
        ttk.Button(text_btn_frame, text="📥 Вставить", command=self.paste_text).pack(side=tk.LEFT, padx=2)
        ttk.Button(text_btn_frame, text="Очистить",
                   command=lambda: self.text_input.delete("1.0", tk.END)).pack(side=tk.RIGHT, padx=2)

        self.live_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame_text, text="Live прослушивание", variable=self.live_var,
                        command=self.toggle_live).pack(side=tk.LEFT, padx=5, pady=5)

        # --- Фрейм кода Морзе ---
        frame_morse = ttk.LabelFrame(master, text="Код Морзе")
        frame_morse.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)

        self.morse_input = tk.Text(frame_morse, height=5, width=60, font=("Courier New", 10))
        self.morse_input.pack(padx=5, pady=5, fill=tk.BOTH, expand=True)

        # --- Фрейм для выбора частоты (НОВОЕ) ---
        freq_frame = tk.Frame(frame_morse)
        freq_frame.pack(padx=5, pady=2, fill=tk.X)
        tk.Label(freq_frame, text="Частота (Гц):").pack(side=tk.LEFT)
        self.freq_spin = tk.Spinbox(freq_frame, from_=100, to=2000, increment=50,
                                    textvariable=self.freq_var, width=6)
        self.freq_spin.pack(side=tk.LEFT, padx=5)

        # --- Кнопки управления ---
        morse_btn_frame = tk.Frame(frame_morse)
        morse_btn_frame.pack(padx=5, pady=2, fill=tk.X)
        btn_decode = ttk.Button(morse_btn_frame, text="← Декодировать", command=self.decode)
        btn_decode.pack(side=tk.LEFT, padx=2)
        self.play_button = ttk.Button(morse_btn_frame, text="▶ Воспроизвести", command=self.play)
        self.play_button.pack(side=tk.LEFT, padx=2)
        self.export_wav_button = ttk.Button(morse_btn_frame, text="📀 Записать WAV", command=self.export_wav)
        self.export_wav_button.pack(side=tk.LEFT, padx=2)
        self.recognize_button = ttk.Button(morse_btn_frame, text="📡 Распознать аудио", command=self.recognize_audio)
        self.recognize_button.pack(side=tk.LEFT, padx=2)
        ttk.Button(morse_btn_frame, text="📋 Копировать", command=self.copy_morse).pack(side=tk.LEFT, padx=2)
        ttk.Button(morse_btn_frame, text="📥 Вставить", command=self.paste_morse).pack(side=tk.LEFT, padx=2)
        ttk.Button(morse_btn_frame, text="Очистить",
                   command=lambda: self.morse_input.delete("1.0", tk.END)).pack(side=tk.RIGHT, padx=2)

        # --- Статусная строка ---
        self.status = ttk.Label(master, text="Готов", relief=tk.SUNKEN, anchor=tk.W)
        self.status.pack(fill=tk.X, padx=10, pady=(0, 10))

    # ---------- Копирование / вставка ----------
    def copy_text(self):
        try:
            text = self.text_input.get("sel.first", "sel.last")
        except tk.TclError:
            text = None
        if not text:
            text = self.text_input.get("1.0", tk.END).strip()
        if text:
            self.master.clipboard_clear()
            self.master.clipboard_append(text)
            self.status.config(text="Текст скопирован")

    def copy_morse(self):
        try:
            text = self.morse_input.get("sel.first", "sel.last")
        except tk.TclError:
            text = None
        if not text:
            text = self.morse_input.get("1.0", tk.END).strip()
        if text:
            self.master.clipboard_clear()
            self.master.clipboard_append(text)
            self.status.config(text="Код Морзе скопирован")

    def paste_text(self):
        try:
            text = self.master.clipboard_get()
        except:
            return
        self.text_input.insert(tk.INSERT, text)

    def paste_morse(self):
        try:
            text = self.master.clipboard_get()
        except:
            return
        self.morse_input.insert(tk.INSERT, text)

    # ---------- Кодирование / декодирование ----------
    def encode(self):
        text = self.text_input.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Пусто", "Введите текст.")
            return
        morse = text_to_morse(text)
        self.morse_input.delete("1.0", tk.END)
        self.morse_input.insert("1.0", morse)
        self.status.config(text="Закодировано")
        if self.live_mode:
            self.schedule_live_playback(morse)

    def decode(self):
        morse = self.morse_input.get("1.0", tk.END).strip()
        if not morse:
            messagebox.showwarning("Пусто", "Введите код Морзе.")
            return
        text = morse_to_text(morse)
        self.text_input.delete("1.0", tk.END)
        self.text_input.insert("1.0", text)
        self.status.config(text="Декодировано")

    # ---------- Распознавание аудио ----------
    def recognize_audio(self):
        if self.playing:
            messagebox.showwarning("Занято", "Воспроизведение уже выполняется.")
            return

        filepath = filedialog.askopenfilename(
            title="Выберите аудиофайл с кодом Морзе",
            filetypes=[("Аудио", "*.wav *.mp3 *.ogg"), ("Все файлы", "*.*")]
        )
        if not filepath:
            return

        self.recognize_button.config(state=tk.DISABLED)
        self.status.config(text="Распознавание...")
        self.master.update_idletasks()

        threading.Thread(target=self._recognize_thread, args=(filepath,), daemon=True).start()

    def _recognize_thread(self, filepath):
        try:
            morse, text = recognize_morse_from_audio(filepath)
        except Exception as e:
            morse, text = None, str(e)

        self.master.after(0, lambda: self._on_recognize_done(morse, text))

    def _on_recognize_done(self, morse, text):
        self.recognize_button.config(state=tk.NORMAL)
        if morse is None:
            messagebox.showerror("Ошибка", f"Не удалось распознать:\n{text}")
            self.status.config(text="Ошибка распознавания")
        else:
            self.morse_input.delete("1.0", tk.END)
            self.morse_input.insert("1.0", morse)
            self.text_input.delete("1.0", tk.END)
            self.text_input.insert("1.0", text)
            self.status.config(text="Распознано успешно")

    # ---------- Live-режим ----------
    def toggle_live(self):
        self.live_mode = self.live_var.get()
        if not self.live_mode:
            if self.live_after_id:
                self.master.after_cancel(self.live_after_id)
                self.live_after_id = None

    def on_text_change(self, event=None):
        if not self.live_mode:
            return
        if self.live_after_id:
            self.master.after_cancel(self.live_after_id)
        self.live_after_id = self.master.after(300, self.live_encode_and_play)

    def live_encode_and_play(self):
        text = self.text_input.get("1.0", tk.END).strip()
        if not text:
            return
        morse = text_to_morse(text)
        self.morse_input.delete("1.0", tk.END)
        self.morse_input.insert("1.0", morse)
        self.play_morse(morse)  # будет использовать текущую частоту

    # ---------- Воспроизведение ----------
    def play(self):
        morse = self.morse_input.get("1.0", tk.END).strip()
        if not morse:
            messagebox.showwarning("Нет кода", "Введите код Морзе.")
            return
        self.play_morse(morse)

    def play_morse(self, morse_str):   # теперь берет частоту из self.freq_var
        if self.playing:
            self.stop_current_player()
        self.playing = True
        self.play_button.config(state=tk.DISABLED)
        self.export_wav_button.config(state=tk.DISABLED)
        self.recognize_button.config(state=tk.DISABLED)
        self.status.config(text="Воспроизведение...")
        freq = self.freq_var.get()          # НОВОЕ
        self.current_player = PlayerThread(morse_str, freq, self.on_play_finished)  # передаём частоту
        self.current_player.start()

    def stop_current_player(self):
        if self.current_player and self.current_player.is_alive():
            self.current_player.stop_event.set()

    def on_play_finished(self):
        self.playing = False
        self.play_button.config(state=tk.NORMAL)
        self.export_wav_button.config(state=tk.NORMAL)
        self.recognize_button.config(state=tk.NORMAL)
        self.status.config(text="Готов")

    # ---------- Экспорт WAV (с учётом частоты) ----------
    def export_wav(self):
        morse = self.morse_input.get("1.0", tk.END).strip()
        if not morse:
            messagebox.showwarning("Нет кода", "Введите код Морзе.")
            return
        filepath = filedialog.asksaveasfilename(defaultextension=".wav",
                                                filetypes=[("WAV файлы", "*.wav")])
        if not filepath:
            return
        try:
            self.status.config(text="Запись WAV...")
            self.master.update_idletasks()
            freq = self.freq_var.get()          # НОВОЕ
            generate_morse_wav(morse, filepath, freq=freq)  # передаём частоту
            self.status.config(text=f"Сохранено: {os.path.basename(filepath)}")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

# ---------- Запуск ----------
if __name__ == "__main__":
    root = tk.Tk()
    app = MorseApp(root)
    root.mainloop()