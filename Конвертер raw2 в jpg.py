import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image
import rawpy
import imageio
from threading import Thread
import pyexiv2  # Для работы с метаданными
import logging
from datetime import datetime

class RawConverterPro:
    def __init__(self, root):
        self.root = root
        root.title("RAW to JPEG Converter Pro")
        root.geometry("600x400")
        
        # Инициализация стилей
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        # Настройка переменных
        self.input_paths = []
        self.output_folder = tk.StringVar()
        self.settings = {
            'quality': tk.IntVar(value=92),
            'auto_wb': tk.BooleanVar(value=True),
            'gamma': tk.DoubleVar(value=2.2),
            'bright': tk.DoubleVar(value=1.0),
            'save_exif': tk.BooleanVar(value=True)
        }
        
        # Инициализация интерфейса
        self.create_widgets()
        self.setup_logging()
        
    def setup_logging(self):
        logging.basicConfig(
            filename='conversion.log',
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )

    def create_widgets(self):
        # Фрейм для выбора файлов
        input_frame = ttk.LabelFrame(self.root, text="Источник")
        input_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(input_frame, text="Выбрать файлы", 
                 command=self.select_files).pack(side=tk.LEFT, padx=5)
        ttk.Button(input_frame, text="Выбрать папку", 
                 command=self.select_folder).pack(side=tk.LEFT, padx=5)
        
        # Фрейм настроек
        settings_frame = ttk.LabelFrame(self.root, text="Настройки")
        settings_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Качество
        ttk.Label(settings_frame, text="Качество JPEG:").grid(row=0, column=0, sticky=tk.W)
        ttk.Spinbox(settings_frame, from_=1, to=100, 
                   textvariable=self.settings['quality'], width=5).grid(row=0, column=1)
        
        # Автоматический баланс белого
        ttk.Checkbutton(settings_frame, text="Авто баланс белого",
                      variable=self.settings['auto_wb']).grid(row=1, column=0, sticky=tk.W)
        
        # Коррекция гаммы
        ttk.Label(settings_frame, text="Гамма:").grid(row=2, column=0, sticky=tk.W)
        ttk.Scale(settings_frame, from_=1.0, to=3.0, variable=self.settings['gamma'],
                orient=tk.HORIZONTAL, length=150).grid(row=2, column=1)
        
        # Яркость
        ttk.Label(settings_frame, text="Яркость:").grid(row=3, column=0, sticky=tk.W)
        ttk.Scale(settings_frame, from_=0.5, to=2.0, variable=self.settings['bright'],
                orient=tk.HORIZONTAL, length=150).grid(row=3, column=1)
        
        # Сохранение EXIF
        ttk.Checkbutton(settings_frame, text="Сохранить EXIF",
                      variable=self.settings['save_exif']).grid(row=4, column=0, sticky=tk.W)
        
        # Выходная папка
        output_frame = ttk.LabelFrame(self.root, text="Выходная папка")
        output_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Entry(output_frame, textvariable=self.output_folder, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(output_frame, text="Обзор", 
                 command=self.select_output_folder).pack(side=tk.LEFT)
        
        # Прогресс и кнопка
        self.progress = ttk.Progressbar(self.root, orient=tk.HORIZONTAL, length=500, mode='determinate')
        self.progress.pack(pady=10)
        
        ttk.Button(self.root, text="Начать конвертацию", 
                 command=self.start_conversion).pack(pady=5)
        
    def select_files(self):
        files = filedialog.askopenfilenames(filetypes=[
            ("RAW файлы", "*.cr2 *.nef *.arw *.dng *.rw2 *.orf"),
            ("Все файлы", "*.*")
        ])
        if files:
            self.input_paths = list(files)
            
    def select_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.input_paths = [os.path.join(folder, f) for f in os.listdir(folder) 
                              if os.path.splitext(f)[1].lower() in {'.cr2','.nef','.arw','.dng','.rw2','.orf'}]
            
    def select_output_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output_folder.set(folder)
            
    def process_image(self, input_path, output_path):
        try:
            with rawpy.imread(input_path) as raw:
                # Параметры обработки
                params = {
                    'user_wb': raw.daylight_whitebalance if self.settings['auto_wb'].get() else None,
                    'gamma': (self.settings['gamma'].get(), self.settings['gamma'].get()),
                    'bright': self.settings['bright'].get(),
                    'output_color': rawpy.ColorSpace.sRGB
                }
                rgb = raw.postprocess(**params)
                
            # Сохранение изображения
            imageio.imwrite(output_path, rgb, quality=self.settings['quality'].get())
            
            # Копирование EXIF данных
            if self.settings['save_exif'].get():
                try:
                    with pyexiv2.Image(input_path) as img:
                        exif_data = img.read_exif()
                    with pyexiv2.Image(output_path) as img:
                        img.modify_exif(exif_data)
                except Exception as ex:
                    logging.warning(f"Ошибка EXIF: {str(ex)}")
                    
            return True
        except Exception as e:
            logging.error(f"Ошибка обработки {input_path}: {str(e)}")
            return False
            
    def start_conversion(self):
        if not self.input_paths or not self.output_folder.get():
            messagebox.showerror("Ошибка", "Выберите исходные файлы и выходную папку!")
            return
            
        output_dir = self.output_folder.get()
        os.makedirs(output_dir, exist_ok=True)
        
        def conversion_thread():
            total = len(self.input_paths)
            for i, input_path in enumerate(self.input_paths):
                filename = os.path.basename(input_path)
                output_path = os.path.join(output_dir, f"{os.path.splitext(filename)[0]}.jpg")
                
                success = self.process_image(input_path, output_path)
                self.progress['value'] = (i+1)/total*100
                self.root.update_idletasks()
                
            messagebox.showinfo("Готово", f"Обработано {total} файлов!")
            
        Thread(target=conversion_thread, daemon=True).start()

if __name__ == "__main__":
    root = tk.Tk()
    app = RawConverterPro(root)
    root.mainloop()
