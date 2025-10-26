import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import time
import subprocess
import os
from datetime import datetime

class TimerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Программа запуска по таймеру")
        self.root.geometry("500x350")
        self.root.resizable(False, False)
        
        # Переменные для хранения состояния
        self.selected_program = ""
        self.scheduler_thread = None
        self.running = False
        
        # Создаем элементы интерфейса
        self.create_widgets()
        
    def create_widgets(self):
        # Выбор программы
        tk.Label(self.root, text="Выберите программу:").pack(pady=(10, 0))
        self.program_entry = tk.Entry(self.root, width=50)
        self.program_entry.pack(pady=5)
        tk.Button(self.root, text="Обзор...", command=self.browse_program).pack()
        
        # Фрейм для даты и времени
        datetime_frame = tk.Frame(self.root)
        datetime_frame.pack(pady=10)
        
        # Выбор даты
        tk.Label(datetime_frame, text="Дата запуска:").grid(row=0, column=0, padx=5, sticky="w")
        self.date_frame = tk.Frame(datetime_frame)
        self.date_frame.grid(row=1, column=0, padx=5)
        
        self.day_var = tk.StringVar(value=str(datetime.now().day))
        self.month_var = tk.StringVar(value=str(datetime.now().month))
        self.year_var = tk.StringVar(value=str(datetime.now().year))
        
        tk.Label(self.date_frame, text="День:").grid(row=0, column=0)
        self.day_entry = tk.Entry(self.date_frame, width=3, textvariable=self.day_var)
        self.day_entry.grid(row=0, column=1, padx=2)
        
        tk.Label(self.date_frame, text="Месяц:").grid(row=0, column=2)
        self.month_entry = tk.Entry(self.date_frame, width=3, textvariable=self.month_var)
        self.month_entry.grid(row=0, column=3, padx=2)
        
        tk.Label(self.date_frame, text="Год:").grid(row=0, column=4)
        self.year_entry = tk.Entry(self.date_frame, width=5, textvariable=self.year_var)
        self.year_entry.grid(row=0, column=5, padx=2)
        
        # Выбор времени
        tk.Label(datetime_frame, text="Время запуска:").grid(row=0, column=1, padx=5, sticky="w")
        self.time_entry = tk.Entry(datetime_frame, width=10)
        self.time_entry.grid(row=1, column=1, padx=5)
        self.time_entry.insert(0, datetime.now().strftime("%H:%M"))
        
        # Режим запуска
        tk.Label(self.root, text="Режим запуска:").pack(pady=(5, 0))
        self.launch_mode = tk.StringVar(value="once")
        tk.Radiobutton(
            self.root, 
            text="Однократный запуск (по дате и времени)", 
            variable=self.launch_mode, 
            value="once"
        ).pack(anchor="w", padx=20)
        tk.Radiobutton(
            self.root, 
            text="Ежедневный запуск (только по времени)", 
            variable=self.launch_mode, 
            value="daily"
        ).pack(anchor="w", padx=20)
        
        # Статус
        self.status_label = tk.Label(self.root, text="Статус: Остановлено", fg="red")
        self.status_label.pack(pady=(10, 5))
        
        # Кнопки управления
        button_frame = tk.Frame(self.root)
        button_frame.pack(pady=5)
        
        self.start_button = tk.Button(
            button_frame, 
            text="Старт", 
            command=self.start_scheduler,
            width=10
        )
        self.start_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = tk.Button(
            button_frame, 
            text="Стоп", 
            command=self.stop_scheduler,
            state=tk.DISABLED,
            width=10
        )
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        # Лог
        tk.Label(self.root, text="Журнал событий:").pack(pady=(10, 0))
        self.log_text = tk.Text(self.root, height=6, width=60)
        self.log_text.pack(pady=5)
        self.log_text.config(state=tk.DISABLED)
        
    def browse_program(self):
        filepath = filedialog.askopenfilename(
            title="Выберите программу",
            filetypes=[("Исполняемые файлы", "*.exe *.bat *.cmd *.mp4 *.mp3"), ("Все файлы", "*.*")]
        )
        if filepath:
            self.selected_program = filepath
            self.program_entry.delete(0, tk.END)
            self.program_entry.insert(0, filepath)
    
    def start_scheduler(self):
        if not self.selected_program:
            messagebox.showerror("Ошибка", "Сначала выберите программу!")
            return
            
        mode = self.launch_mode.get()
        
        try:
            # Проверка формата времени
            time_str = self.time_entry.get()
            target_time = datetime.strptime(time_str, "%H:%M").time()
            
            # Для однократного запуска - проверка даты
            if mode == "once":
                day = int(self.day_var.get())
                month = int(self.month_var.get())
                year = int(self.year_var.get())
                
                # Проверка корректности даты
                if day < 1 or day > 31 or month < 1 or month > 12 or year < 2020:
                    raise ValueError("Некорректная дата")
                
                # Создаем целевую дату-время
                target_datetime = datetime(year, month, day, 
                                         target_time.hour, target_time.minute)
                
                # Проверка, что дата в будущем
                if target_datetime < datetime.now():
                    raise ValueError("Указанная дата и время уже прошли")
        except ValueError as e:
            messagebox.showerror("Ошибка", f"Ошибка ввода данных: {str(e)}")
            return
            
        if self.running:
            return
            
        self.running = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.status_label.config(text="Статус: Запущено", fg="green")
        
        # Запуск потока планировщика
        self.scheduler_thread = threading.Thread(
            target=self.scheduler_loop, 
            args=(target_time, mode, day, month, year) if mode == "once" else (target_time, mode),
            daemon=True
        )
        self.scheduler_thread.start()
        
        if mode == "once":
            self.log(f"Таймер запущен. Программа будет запущена {day}.{month}.{year} в {time_str}")
        else:
            self.log(f"Таймер запущен. Программа будет запускаться ежедневно в {time_str}")
    
    def stop_scheduler(self):
        if not self.running:
            return
            
        self.running = False
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.status_label.config(text="Статус: Остановлено", fg="red")
        self.log("Таймер остановлен")
    
    def run_program(self):
        try:
            program_name = os.path.basename(self.selected_program)
            self.log(f"Запуск программы: {program_name}")
            subprocess.Popen(self.selected_program, shell=True)
        except Exception as e:
            self.log(f"Ошибка: {str(e)}")
    
    def scheduler_loop(self, target_time, mode, day=None, month=None, year=None):
        if mode == "once":
            # Для разового запуска
            target_datetime = datetime(year, month, day, target_time.hour, target_time.minute)
            
            while self.running:
                now = datetime.now()
                
                # Если указанное время наступило
                if now >= target_datetime:
                    self.run_program()
                    self.stop_scheduler()  # Автоматически останавливаем после запуска
                    break
                
                # Проверяем каждую секунду
                time.sleep(1)
        else:
            # Для ежедневного запуска
            while self.running:
                now = datetime.now()
                current_time = now.time()
                
                # Проверяем, наступило ли целевое время
                if current_time.hour == target_time.hour and current_time.minute == target_time.minute:
                    self.run_program()
                    
                    # Ждем минуту, чтобы избежать повторного запуска
                    time.sleep(60)
                else:
                    # Проверяем каждые 10 секунд
                    time.sleep(10)

    def log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] {message}\n"
        
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, log_message)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

if __name__ == "__main__":
    root = tk.Tk()
    app = TimerApp(root)
    root.mainloop()
