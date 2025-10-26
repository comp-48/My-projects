import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from PIL import Image, ImageTk
import threading
import os
import sys
import math

class SteganographyApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Продвинутый инструмент стеганографии")
        self.root.geometry("700x650")
        self.root.resizable(True, True)
        
        # Настройка стилей
        self.setup_style()
        
        # Создание вкладок
        self.tab_control = ttk.Notebook(root)
        self.encode_tab = ttk.Frame(self.tab_control)
        self.decode_tab = ttk.Frame(self.tab_control)
        
        self.tab_control.add(self.encode_tab, text='🖼️ Кодировать сообщение')
        self.tab_control.add(self.decode_tab, text='🔍 Декодировать сообщение')
        self.tab_control.pack(expand=1, fill="both", padx=10, pady=10)
        
        # Создание интерфейса
        self.create_encode_tab()
        self.create_decode_tab()
        
        # Строка состояния
        self.status_var = tk.StringVar()
        self.status_var.set("Готов к работе")
        status_bar = ttk.Label(root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, style='Status.TLabel')
        status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=2, pady=2)
        
        # Инициализация переменных
        self.cancel_operation = False
        self.progress = None
    
    def setup_style(self):
        """Настройка стилей интерфейса"""
        style = ttk.Style()
        style.configure("TButton", padding=6, font=('Arial', 10))
        style.configure("TLabel", font=('Arial', 10))
        style.configure("TFrame", background="#f0f0f0")
        style.configure("TLabelframe", font=('Arial', 10, 'bold'))
        style.configure("TLabelframe.Label", font=('Arial', 10, 'bold'))
        style.configure("Status.TLabel", background="#e0e0e0", font=('Arial', 9))
        style.configure("Bold.TLabel", font=('Arial', 10, 'bold'))
    
    def create_encode_tab(self):
        """Создание интерфейса для кодирования"""
        frame = ttk.LabelFrame(self.encode_tab, text="Кодирование текста в изображение")
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Выбор изображения
        file_frame = ttk.Frame(frame)
        file_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(file_frame, text="Исходное изображение:", style='Bold.TLabel').pack(side=tk.LEFT, padx=(0, 5))
        self.source_path = tk.StringVar()
        source_entry = ttk.Entry(file_frame, textvariable=self.source_path, width=50)
        source_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(file_frame, text="Выбрать файл...", command=self.browse_source_image, width=12).pack(side=tk.LEFT)
        
        # Превью изображения
        self.preview_frame = ttk.LabelFrame(frame, text="Превью изображения")
        self.preview_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.preview_label = ttk.Label(self.preview_frame, text="Изображение не выбрано")
        self.preview_label.pack(padx=10, pady=10)
        
        # Поле для сообщения с русскими подсказками
        msg_frame = ttk.LabelFrame(frame, text="Секретный текст")
        msg_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Добавляем подсказку на русском языке
        placeholder_text = "Введите здесь текст, который вы хотите скрыть в изображении..."
        self.message_text = scrolledtext.ScrolledText(msg_frame, height=5, wrap=tk.WORD, font=('Arial', 10))
        self.message_text.insert("1.0", placeholder_text)
        self.message_text.config(fg="grey")
        self.message_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Обработчики событий для плейсхолдера
        self.message_text.bind("<FocusIn>", self.on_message_focus_in)
        self.message_text.bind("<FocusOut>", self.on_message_focus_out)
        
        # Дополнительные опции
        options_frame = ttk.Frame(frame)
        options_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        
        ttk.Label(options_frame, text="Пароль (необязательно):", style='Bold.TLabel').pack(side=tk.LEFT, padx=(0, 5))
        self.password_var = tk.StringVar()
        pwd_entry = ttk.Entry(options_frame, textvariable=self.password_var, show="•", width=20)
        pwd_entry.pack(side=tk.LEFT)
        
        # Расчет размера
        self.size_label = ttk.Label(options_frame, text="Доступно: 0 символов")
        self.size_label.pack(side=tk.RIGHT, padx=10)
        
        # Сохранение результата
        output_frame = ttk.Frame(frame)
        output_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(output_frame, text="Файл для сохранения:", style='Bold.TLabel').pack(side=tk.LEFT, padx=(0, 5))
        self.output_path = tk.StringVar()
        output_entry = ttk.Entry(output_frame, textvariable=self.output_path, width=50)
        output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(output_frame, text="Указать путь...", command=self.browse_output_image, width=12).pack(side=tk.LEFT)
        
        # Прогресс бар
        self.progress_frame = ttk.Frame(frame)
        self.progress_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Кнопки управления
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=10)
        
        self.encode_btn = ttk.Button(btn_frame, text="Начать кодирование", command=self.start_encode_thread, style='TButton')
        self.encode_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.cancel_btn = ttk.Button(btn_frame, text="Отменить операцию", command=self.cancel_operation, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT)
        
        # Привязка событий
        self.source_path.trace_add("write", self.update_size_calculation)
    
    def create_decode_tab(self):
        """Создание интерфейса для декодирования"""
        frame = ttk.LabelFrame(self.decode_tab, text="Декодирование текста из изображения")
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Выбор изображения
        file_frame = ttk.Frame(frame)
        file_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(file_frame, text="Закодированное изображение:", style='Bold.TLabel').pack(side=tk.LEFT, padx=(0, 5))
        self.encoded_path = tk.StringVar()
        encoded_entry = ttk.Entry(file_frame, textvariable=self.encoded_path, width=50)
        encoded_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(file_frame, text="Выбрать файл...", command=self.browse_encoded_image, width=12).pack(side=tk.LEFT)
        
        # Превью изображения
        self.decode_preview_frame = ttk.LabelFrame(frame, text="Превью изображения")
        self.decode_preview_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.decode_preview_label = ttk.Label(self.decode_preview_frame, text="Изображение не выбрано")
        self.decode_preview_label.pack(padx=10, pady=10)
        
        # Опции декодирования
        options_frame = ttk.Frame(frame)
        options_frame.pack(fill=tk.X, padx=5, pady=(5, 0))
        
        ttk.Label(options_frame, text="Пароль (если использовался):", style='Bold.TLabel').pack(side=tk.LEFT, padx=(0, 5))
        self.decode_password_var = tk.StringVar()
        pwd_entry = ttk.Entry(options_frame, textvariable=self.decode_password_var, show="•", width=20)
        pwd_entry.pack(side=tk.LEFT)
        
        # Поле для результата с русскими подсказками
        result_frame = ttk.LabelFrame(frame, text="Извлеченный текст")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.result_text = scrolledtext.ScrolledText(result_frame, height=5, wrap=tk.WORD, font=('Arial', 10))
        
        # Добавляем подсказку на русском языке
        self.result_text.insert("1.0", "Здесь появится текст, извлеченный из изображения...")
        self.result_text.config(fg="grey", state=tk.NORMAL)
        self.result_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.result_text.config(state=tk.DISABLED)
        
        # Обработчики событий для плейсхолдера
        self.result_text.bind("<FocusIn>", self.on_result_focus_in)
        self.result_text.bind("<FocusOut>", self.on_result_focus_out)
        
        # Прогресс бар
        self.decode_progress_frame = ttk.Frame(frame)
        self.decode_progress_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Кнопки управления
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=10)
        
        self.decode_btn = ttk.Button(btn_frame, text="Начать декодирование", command=self.start_decode_thread)
        self.decode_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.decode_cancel_btn = ttk.Button(btn_frame, text="Отменить операцию", command=self.cancel_operation, state=tk.DISABLED)
        self.decode_cancel_btn.pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Очистить результаты", command=self.clear_decode).pack(side=tk.RIGHT)
    
    def on_message_focus_in(self, event):
        """Обработчик фокусировки на поле ввода сообщения"""
        placeholder = "Введите здесь текст, который вы хотите скрыть в изображении..."
        if self.message_text.get("1.0", "end-1c") == placeholder:
            self.message_text.delete("1.0", "end")
            self.message_text.config(fg="black")
    
    def on_message_focus_out(self, event):
        """Обработчик потери фокуса на поле ввода сообщения"""
        if not self.message_text.get("1.0", "end-1c").strip():
            placeholder = "Введите здесь текст, который вы хотите скрыть в изображении..."
            self.message_text.insert("1.0", placeholder)
            self.message_text.config(fg="grey")
    
    def on_result_focus_in(self, event):
        """Обработчик фокусировки на поле результата"""
        placeholder = "Здесь появится текст, извлеченный из изображения..."
        current_text = self.result_text.get("1.0", "end-1c")
        
        if current_text == placeholder:
            self.result_text.config(state=tk.NORMAL)
            self.result_text.delete("1.0", "end")
            self.result_text.config(fg="black")
    
    def on_result_focus_out(self, event):
        """Обработчик потери фокуса на поле результата"""
        current_text = self.result_text.get("1.0", "end-1c")
        placeholder = "Здесь появится текст, извлеченный из изображения..."
        
        if not current_text.strip():
            self.result_text.config(state=tk.NORMAL)
            self.result_text.insert("1.0", placeholder)
            self.result_text.config(fg="grey")
            self.result_text.config(state=tk.DISABLED)
    
    def browse_source_image(self):
        """Выбор исходного изображения для кодирования"""
        path = filedialog.askopenfilename(
            title="Выберите изображение для кодирования",
            filetypes=[("Изображения", "*.png;*.jpg;*.jpeg;*.bmp;*.tiff"), ("Все файлы", "*.*")]
        )
        if path:
            self.source_path.set(path)
            base, ext = os.path.splitext(path)
            self.output_path.set(f"{base}_закодированное.png")
            self.show_preview(path, self.preview_label, self.preview_frame)
            self.update_size_calculation()
    
    def browse_output_image(self):
        """Выбор пути для сохранения результата"""
        path = filedialog.asksaveasfilename(
            title="Сохранить закодированное изображение",
            defaultextension=".png",
            filetypes=[("PNG файлы", "*.png"), ("Все файлы", "*.*")]
        )
        if path:
            self.output_path.set(path)
    
    def browse_encoded_image(self):
        """Выбор закодированного изображения для декодирования"""
        path = filedialog.askopenfilename(
            title="Выберите закодированное изображение",
            filetypes=[("Изображения", "*.png;*.jpg;*.jpeg;*.bmp;*.tiff"), ("Все файлы", "*.*")]
        )
        if path:
            self.encoded_path.set(path)
            self.show_preview(path, self.decode_preview_label, self.decode_preview_frame)
    
    def show_preview(self, path, label_widget, frame_widget):
        """Отображение превью изображения"""
        try:
            img = Image.open(path)
            img.thumbnail((250, 250))
            photo = ImageTk.PhotoImage(img)
            label_widget.configure(image=photo, text="")
            label_widget.image = photo
            frame_widget.configure(text=f"Превью: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Ошибка загрузки", f"Не удалось загрузить изображение:\n{str(e)}")
            label_widget.configure(image=None, text="Неверный формат изображения")
    
    def update_size_calculation(self, *args):
        """Обновление информации о доступном размере сообщения"""
        source = self.source_path.get()
        if not source or not os.path.exists(source):
            self.size_label.config(text="Доступно: 0 символов")
            return
        
        try:
            img = Image.open(source)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Расчет доступного места (3 бита на пиксель)
            max_bits = img.width * img.height * 3
            max_chars = max_bits // 8 - 1  # Минус 1 для маркера конца
            
            # Учет возможного шифрования
            password = self.password_var.get()
            if password:
                max_chars = min(max_chars, max_chars - len(password))
            
            self.size_label.config(text=f"Доступно: {max_chars} символов")
        except:
            self.size_label.config(text="Доступно: 0 символов")
    
    def start_encode_thread(self):
        """Запуск кодирования в отдельном потоке"""
        # Проверяем, не введен ли плейсхолдер как сообщение
        placeholder = "Введите здесь текст, который вы хотите скрыть в изображении..."
        current_text = self.message_text.get("1.0", "end-1c")
        
        if current_text.strip() == "" or current_text == placeholder:
            messagebox.showwarning("Пустой текст", "Пожалуйста, введите текст для кодирования!")
            return
            
        self.cancel_operation = False
        self.encode_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.status_var.set("Начато кодирование...")
        
        # Создание индикатора прогресса
        self.progress = ttk.Progressbar(
            self.progress_frame, 
            orient=tk.HORIZONTAL, 
            length=200, 
            mode='indeterminate'
        )
        self.progress.pack(pady=5)
        self.progress.start()
        
        # Запуск в отдельном потоке
        threading.Thread(target=self.encode_message, daemon=True).start()
    
    def start_decode_thread(self):
        """Запуск декодирования в отдельном потоке"""
        self.cancel_operation = False
        self.decode_btn.config(state=tk.DISABLED)
        self.decode_cancel_btn.config(state=tk.NORMAL)
        self.status_var.set("Начато декодирование...")
        
        # Создание индикатора прогресса
        self.decode_progress = ttk.Progressbar(
            self.decode_progress_frame, 
            orient=tk.HORIZONTAL, 
            length=200, 
            mode='indeterminate'
        )
        self.decode_progress.pack(pady=5)
        self.decode_progress.start()
        
        # Запуск в отдельном потоке
        threading.Thread(target=self.decode_message, daemon=True).start()
    
    def cancel_operation(self):
        """Отмена текущей операции"""
        self.cancel_operation = True
        self.status_var.set("Операция отменена пользователем")
    
    def clear_decode(self):
        """Очистка результатов декодирования"""
        self.result_text.config(state=tk.NORMAL)
        self.result_text.delete(1.0, tk.END)
        
        # Восстанавливаем плейсхолдер
        placeholder = "Здесь появится текст, извлеченный из изображения..."
        self.result_text.insert("1.0", placeholder)
        self.result_text.config(fg="grey")
        self.result_text.config(state=tk.DISABLED)
        
        self.decode_password_var.set("")
        self.status_var.set("Поле результатов очищено")
    
    def encrypt_decrypt(self, text, password):
        """Шифрование/дешифрование текста с использованием пароля"""
        if not password:
            return text
            
        encrypted = []
        pwd_len = len(password)
        for i, char in enumerate(text):
            key_char = password[i % pwd_len]
            encrypted_char = chr(ord(char) ^ ord(key_char))
            encrypted.append(encrypted_char)
        return ''.join(encrypted)
    
    def encode_message(self):
        """Основная функция кодирования сообщения в изображение"""
        source = self.source_path.get()
        output = self.output_path.get()
        password = self.password_var.get()
        
        # Получаем текст сообщения
        current_text = self.message_text.get("1.0", "end-1c")
        placeholder = "Введите здесь текст, который вы хотите скрыть в изображении..."
        message = current_text.strip() if current_text != placeholder else ""
        
        # Проверка входных данных
        if not source or not os.path.exists(source):
            messagebox.showerror("Ошибка", "Выберите существующий файл изображения!")
            self.finish_operation()
            return
            
        if not message:
            messagebox.showerror("Ошибка", "Введите текст для кодирования!")
            self.finish_operation()
            return
            
        if not output:
            messagebox.showerror("Ошибка", "Укажите путь для сохранения результата!")
            self.finish_operation()
            return
        
        try:
            # Шифрование сообщения
            if password:
                message = self.encrypt_decrypt(message, password)
            
            img = Image.open(source)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Преобразование сообщения в бинарный формат
            binary_msg = ''.join(format(ord(c), '08b') for c in message)
            binary_msg += '00000000'  # Маркер окончания
            
            # Проверка размера сообщения
            max_bits = img.width * img.height * 3
            if len(binary_msg) > max_bits:
                available_chars = max_bits // 8 - 1
                messagebox.showerror(
                    "Ошибка размера", 
                    f"Сообщение слишком большое для изображения!\n"
                    f"Максимально допустимый размер: {available_chars} символов\n"
                    f"Текущий размер: {len(message)} символов"
                )
                self.finish_operation()
                return
            
            # Кодирование сообщения в изображение
            pixels = list(img.getdata())
            data_index = 0
            new_pixels = []
            total_pixels = len(pixels)
            
            for i, pixel in enumerate(pixels):
                if self.cancel_operation:
                    break
                    
                if data_index < len(binary_msg):
                    r, g, b = pixel
                    # Модификация младших битов
                    r = (r & 0xFE) | int(binary_msg[data_index]); data_index += 1
                    if data_index < len(binary_msg):
                        g = (g & 0xFE) | int(binary_msg[data_index]); data_index += 1
                    if data_index < len(binary_msg):
                        b = (b & 0xFE) | int(binary_msg[data_index]); data_index += 1
                    new_pixels.append((r, g, b))
                else:
                    new_pixels.append(pixel)
                
                # Обновление статуса каждые 1000 пикселей
                if i % 1000 == 0:
                    progress_percent = int(i / total_pixels * 100)
                    self.status_var.set(f"Кодирование: {progress_percent}% завершено")
            
            if self.cancel_operation:
                self.status_var.set("Кодирование отменено")
                return
                
            # Сохранение результата
            encoded_img = Image.new('RGB', img.size)
            encoded_img.putdata(new_pixels)
            encoded_img.save(output, "PNG")
            
            self.status_var.set("Кодирование успешно завершено!")
            messagebox.showinfo(
                "Успех", 
                f"Сообщение успешно закодировано в изображение!\n"
                f"Файл сохранен как: {output}"
            )
            
            # Очистка поля сообщения
            self.message_text.delete("1.0", tk.END)
            self.message_text.config(fg="grey")
            self.message_text.insert("1.0", placeholder)
            
        except Exception as e:
            self.status_var.set(f"Ошибка кодирования: {str(e)}")
            messagebox.showerror("Ошибка кодирования", f"Произошла ошибка при кодировании:\n{str(e)}")
        finally:
            self.finish_operation()
    
    def decode_message(self):
        """Основная функция декодирования сообщения из изображения"""
        path = self.encoded_path.get()
        password = self.decode_password_var.get()
        
        # Проверка входных данных
        if not path or not os.path.exists(path):
            messagebox.showerror("Ошибка", "Выберите существующий файл изображения!")
            self.finish_operation(decode=True)
            return
        
        try:
            img = Image.open(path)
            if img.mode != 'RGB':
                img = img.convert('RGB')
                
            pixels = list(img.getdata())
            binary_data = []
            message = []
            byte = ''
            found_end = False
            total_pixels = len(pixels)
            
            # Извлечение битов данных из изображения
            for i, pixel in enumerate(pixels):
                if self.cancel_operation:
                    break
                    
                r, g, b = pixel
                binary_data.append(r & 1)
                binary_data.append(g & 1)
                binary_data.append(b & 1)
                
                # Обновление статуса
                if i % 1000 == 0:
                    progress_percent = int(i / total_pixels * 100)
                    self.status_var.set(f"Декодирование: {progress_percent}% завершено")
            
            if self.cancel_operation:
                self.status_var.set("Декодирование отменено")
                return
                
            # Преобразование битов в текст
            for bit in binary_data:
                byte += str(bit)
                if len(byte) == 8:
                    if byte == '00000000':
                        found_end = True
                        break
                    message.append(chr(int(byte, 2)))
                    byte = ''
            
            decoded_message = ''.join(message)
            
            # Дешифрование сообщения
            if password:
                decoded_message = self.encrypt_decrypt(decoded_message, password)
            
            # Вывод результата
            self.result_text.config(state=tk.NORMAL)
            self.result_text.delete(1.0, tk.END)
            
            if decoded_message:
                self.result_text.insert(tk.END, decoded_message)
                self.result_text.config(fg="black")
            else:
                # Если сообщение пустое, показываем плейсхолдер
                placeholder = "Здесь появится текст, извлеченный из изображения..."
                self.result_text.insert(tk.END, placeholder)
                self.result_text.config(fg="grey")
            
            self.result_text.config(state=tk.DISABLED)
            
            if not found_end:
                messagebox.showwarning(
                    "Предупреждение", 
                    "Маркер конца сообщения не найден!\n"
                    "Возможно, сообщение было повреждено или использовался другой метод кодирования."
                )
            
            self.status_var.set("Декодирование успешно завершено!")
            messagebox.showinfo(
                "Успех", 
                f"Сообщение успешно извлечено из изображения!\n"
                f"Длина сообщения: {len(decoded_message)} символов"
            )
            
        except Exception as e:
            self.status_var.set(f"Ошибка декодирования: {str(e)}")
            messagebox.showerror("Ошибка декодирования", f"Произошла ошибка при декодировании:\n{str(e)}")
        finally:
            self.finish_operation(decode=True)
    
    def finish_operation(self, decode=False):
        """Завершение операции и очистка ресурсов"""
        if decode:
            if hasattr(self, 'decode_progress'):
                self.decode_progress.stop()
                self.decode_progress.pack_forget()
            self.decode_btn.config(state=tk.NORMAL)
            self.decode_cancel_btn.config(state=tk.DISABLED)
        else:
            if self.progress:
                self.progress.stop()
                self.progress.pack_forget()
            self.encode_btn.config(state=tk.NORMAL)
            self.cancel_btn.config(state=tk.DISABLED)

if __name__ == "__main__":
    root = tk.Tk()
    # Иконка приложения
    try:
        root.iconbitmap(default='stegano_icon.ico')
    except:
        pass
    
    app = SteganographyApp(root)
    root.mainloop()
