import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import subprocess
import os
from datetime import datetime
import json
import platform
import sys
import ctypes

class TimerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Запуск по таймеру")
        self.root.geometry("800x600")
        
        # Хранение задач
        self.tasks = []
        self.running = False
        self.thread = None
        self.hidden = False
        self.timer_start_time = None
        
        # Скрытие с панели задач (только для Windows)
        self.hide_from_taskbar()
        
        # Загрузка сохраненных задач
        self.load_tasks()
        
        self.create_widgets()
        self.update_task_list()
        
        # Обработка закрытия окна
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        
        # Создание иконки в трее
        self.create_tray_icon()
        
        # Создание уведомлений
        self.notification_window = None
        
    def hide_from_taskbar(self):
        """Скрытие окна с панели задач (Windows)"""
        try:
            if platform.system() == "Windows":
                hwnd = ctypes.windll.user32.FindWindowW(None, self.root.title())
                if hwnd:
                    ctypes.windll.user32.SetWindowLongW(hwnd, -20, 0x80000)
                    ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0002 | 0x0001)
        except:
            pass
    
    def create_tray_icon(self):
        """Создание иконки в системном трее"""
        try:
            import pystray
            from PIL import Image, ImageDraw
            
            image = self.create_icon_image()
            
            menu = pystray.Menu(
                pystray.MenuItem("📊 Показать окно", self.show_window),
                pystray.MenuItem("▶ Запустить таймер", self.start_timer_from_tray),
                pystray.MenuItem("⏹ Остановить таймер", self.stop_timer_from_tray),
                pystray.MenuItem("📋 Показать задачи", self.show_tasks_notification),
                pystray.MenuItem("❌ Выход", self.quit_app)
            )
            
            self.tray_icon = pystray.Icon("timer_app", image, "⏰ Таймер", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
            
        except ImportError:
            print("Для работы трея установите: pip install pystray pillow")
            self.create_fallback_tray()
        except Exception as e:
            print(f"Ошибка создания трея: {e}")
            self.create_fallback_tray()
    
    def create_fallback_tray(self):
        """Создание простой кнопки для сворачивания"""
        self.status_label.config(text="Статус: Трей не доступен (установите pystray и pillow)")
    
    def create_icon_image(self):
        """Создание иконки для трея"""
        try:
            from PIL import Image, ImageDraw
            
            size = 64
            image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            
            # Рисуем круг
            draw.ellipse([4, 4, size-4, size-4], fill=(52, 152, 219, 255))
            
            # Рисуем часы
            center = size // 2
            draw.ellipse([center-20, center-20, center+20, center+20], fill=(255, 255, 255, 255))
            draw.ellipse([center-16, center-16, center+16, center+16], fill=(52, 152, 219, 255))
            
            # Рисуем стрелки
            draw.line([center, center, center, center-15], fill=(255, 255, 255, 255), width=3)
            draw.line([center, center, center+12, center+2], fill=(255, 255, 255, 255), width=2)
            
            draw.ellipse([center-3, center-3, center+3, center+3], fill=(255, 255, 255, 255))
            
            return image
        except:
            return None
    
    def create_widgets(self):
        # Основной фрейм
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Заголовок с кнопкой сворачивания
        title_frame = ttk.Frame(main_frame)
        title_frame.grid(row=0, column=0, columnspan=5, pady=10, sticky=(tk.W, tk.E))
        
        ttk.Label(title_frame, text="⏰ Запуск по таймеру", 
                 font=('Arial', 14, 'bold')).pack(side=tk.LEFT)
        
        # Кнопка уведомления о статусе таймера
        self.notify_button = ttk.Button(title_frame, text="🔔 Показать статус", 
                                       command=self.show_timer_status)
        self.notify_button.pack(side=tk.RIGHT, padx=5)
        
        ttk.Button(title_frame, text="🔽 Свернуть в трей", 
                  command=self.hide_window).pack(side=tk.RIGHT, padx=5)
        
        ttk.Button(title_frame, text="❌ Закрыть", 
                  command=self.quit_app).pack(side=tk.RIGHT, padx=5)
        
        # Фрейм для добавления задачи
        add_frame = ttk.LabelFrame(main_frame, text="Добавить задачу", padding="10")
        add_frame.grid(row=1, column=0, columnspan=5, sticky=(tk.W, tk.E), pady=10)
        
        # Тип файла
        ttk.Label(add_frame, text="Тип:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.file_type_var = tk.StringVar(value="program")
        file_types = [("Программа", "program"), ("Видео", "video"), ("Аудио", "audio")]
        for i, (text, value) in enumerate(file_types):
            ttk.Radiobutton(add_frame, text=text, variable=self.file_type_var, 
                          value=value, command=self.on_file_type_change).grid(row=0, column=i+1, padx=5)
        
        # Выбор файла/программы
        ttk.Label(add_frame, text="Файл:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.file_var = tk.StringVar()
        self.file_entry = ttk.Entry(add_frame, textvariable=self.file_var, width=40)
        self.file_entry.grid(row=1, column=1, columnspan=2, padx=5, pady=5)
        ttk.Button(add_frame, text="📂 Обзор...", command=self.browse_file).grid(row=1, column=3, padx=5, pady=5)
        
        # Выбор плеера (для видео/аудио)
        self.player_frame = ttk.Frame(add_frame)
        self.player_frame.grid(row=2, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Label(self.player_frame, text="Плеер:").pack(side=tk.LEFT, padx=5)
        self.player_var = tk.StringVar()
        self.player_combo = ttk.Combobox(self.player_frame, textvariable=self.player_var, width=30)
        self.player_combo.pack(side=tk.LEFT, padx=5)
        ttk.Button(self.player_frame, text="🔍 Обзор плеера...", command=self.browse_player).pack(side=tk.LEFT, padx=5)
        
        # Дата и время запуска
        ttk.Label(add_frame, text="Дата и время:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=5)
        
        # Дата
        date_frame = ttk.Frame(add_frame)
        date_frame.grid(row=3, column=1, columnspan=2, sticky=tk.W)
        
        ttk.Label(date_frame, text="День:").pack(side=tk.LEFT, padx=(0,5))
        self.day_spin = ttk.Spinbox(date_frame, from_=1, to=31, width=5)
        self.day_spin.set(datetime.now().day)
        self.day_spin.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(date_frame, text="Месяц:").pack(side=tk.LEFT, padx=5)
        self.month_spin = ttk.Spinbox(date_frame, from_=1, to=12, width=5)
        self.month_spin.set(datetime.now().month)
        self.month_spin.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(date_frame, text="Год:").pack(side=tk.LEFT, padx=5)
        self.year_spin = ttk.Spinbox(date_frame, from_=2020, to=2030, width=5)
        self.year_spin.set(datetime.now().year)
        self.year_spin.pack(side=tk.LEFT, padx=5)
        
        # Время
        time_frame = ttk.Frame(add_frame)
        time_frame.grid(row=3, column=3, sticky=tk.W)
        
        ttk.Label(time_frame, text="Часы:").pack(side=tk.LEFT, padx=5)
        self.hour_spin = ttk.Spinbox(time_frame, from_=0, to=23, width=5)
        self.hour_spin.set(datetime.now().hour)
        self.hour_spin.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(time_frame, text="Мин:").pack(side=tk.LEFT, padx=5)
        self.min_spin = ttk.Spinbox(time_frame, from_=0, to=59, width=5)
        self.min_spin.set(datetime.now().minute)
        self.min_spin.pack(side=tk.LEFT, padx=5)
        
        # Кнопка добавления
        ttk.Button(add_frame, text="➕ Добавить задачу", command=self.add_task).grid(row=4, column=0, columnspan=4, pady=10)
        
        # Фрейм для управления
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=2, column=0, columnspan=5, pady=10)
        
        self.start_button = ttk.Button(control_frame, text="▶ Запустить таймер", command=self.start_timer)
        self.start_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = ttk.Button(control_frame, text="⏹ Остановить таймер", command=self.stop_timer, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(control_frame, text="🗑 Очистить все", command=self.clear_tasks).pack(side=tk.LEFT, padx=5)
        
        # Статус
        self.status_label = ttk.Label(main_frame, text="📊 Статус: Остановлен", font=('Arial', 10))
        self.status_label.grid(row=3, column=0, columnspan=5, pady=5)
        
        # Информация о трее
        info_label = ttk.Label(main_frame, text="💡 Программа работает в фоновом режиме. Иконка в трее.", 
                              font=('Arial', 9), foreground='gray')
        info_label.grid(row=3, column=0, columnspan=5, pady=(0, 5))
        
        # Список задач
        list_frame = ttk.LabelFrame(main_frame, text="📋 Список задач", padding="10")
        list_frame.grid(row=4, column=0, columnspan=5, sticky=(tk.W, tk.E, tk.N, tk.S), pady=10)
        
        # Создание таблицы
        columns = ("Дата/Время", "Тип", "Файл", "Статус")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=10)
        
        # Настройка колонок
        self.tree.heading("Дата/Время", text="📅 Дата/Время")
        self.tree.heading("Тип", text="📂 Тип")
        self.tree.heading("Файл", text="📄 Файл")
        self.tree.heading("Статус", text="📊 Статус")
        
        self.tree.column("Дата/Время", width=130)
        self.tree.column("Тип", width=80)
        self.tree.column("Файл", width=300)
        self.tree.column("Статус", width=100)
        
        # Скроллбар
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        
        # Настройка весов для растягивания
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(4, weight=1)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        
        # Кнопки сохранения/загрузки
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=5, column=0, columnspan=5, pady=5, sticky=(tk.W, tk.E))
        
        ttk.Button(button_frame, text="💾 Сохранить задачи", command=self.save_tasks).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="📂 Загрузить задачи", command=self.load_tasks_from_file).pack(side=tk.LEFT, padx=5)
        
        # Контекстное меню для таблицы
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="🗑 Удалить", command=self.delete_selected)
        self.context_menu.add_command(label="🔄 Переключить", command=self.toggle_selected)
        self.tree.bind("<Button-3>", self.show_context_menu)
        
        # Двойной клик для переключения
        self.tree.bind("<Double-1>", self.on_double_click)
        
        # Инициализация состояния
        self.on_file_type_change()
    
    def show_context_menu(self, event):
        """Показ контекстного меню"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)
    
    def on_double_click(self, event):
        """Обработка двойного клика"""
        item = self.tree.identify_row(event.y)
        if item:
            index = int(self.tree.item(item, "tags")[0])
            self.toggle_task(index)
    
    def delete_selected(self):
        """Удаление выбранной задачи"""
        selection = self.tree.selection()
        if selection:
            item = selection[0]
            index = int(self.tree.item(item, "tags")[0])
            self.delete_task(index)
    
    def toggle_selected(self):
        """Переключение выбранной задачи"""
        selection = self.tree.selection()
        if selection:
            item = selection[0]
            index = int(self.tree.item(item, "tags")[0])
            self.toggle_task(index)
    
    def on_file_type_change(self):
        """Обработка изменения типа файла"""
        file_type = self.file_type_var.get()
        if file_type == "program":
            self.player_frame.grid_remove()
        else:
            self.player_frame.grid()
            self.update_player_list()
    
    def update_player_list(self):
        """Обновление списка плееров"""
        file_type = self.file_type_var.get()
        players = []
        
        system = platform.system()
        if system == "Windows":
            if file_type == "video":
                players = ["VLC Media Player", "Windows Media Player", "MPC-HC"]
            else:
                players = ["VLC Media Player", "Windows Media Player", "AIMP", "Winamp"]
        elif system == "Darwin":
            if file_type == "video":
                players = ["VLC Media Player", "QuickTime Player", "IINA"]
            else:
                players = ["VLC Media Player", "iTunes", "Clementine"]
        else:
            if file_type == "video":
                players = ["VLC Media Player", "MPV", "Totem", "SMPlayer"]
            else:
                players = ["VLC Media Player", "MPV", "Rhythmbox", "Clementine"]
        
        self.player_combo['values'] = players
        if players:
            self.player_combo.set(players[0])
    
    def browse_file(self):
        """Выбор файла через диалоговое окно"""
        file_type = self.file_type_var.get()
        
        if file_type == "program":
            filename = filedialog.askopenfilename(
                title="Выберите программу",
                filetypes=[("Исполняемые файлы", "*.exe *.com *.bat *.sh *.app"), ("Все файлы", "*.*")]
            )
        elif file_type == "video":
            filename = filedialog.askopenfilename(
                title="Выберите видео файл",
                filetypes=[("Видео файлы", "*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm"), ("Все файлы", "*.*")]
            )
        else:
            filename = filedialog.askopenfilename(
                title="Выберите аудио файл",
                filetypes=[("Аудио файлы", "*.mp3 *.wav *.flac *.aac *.ogg *.wma"), ("Все файлы", "*.*")]
            )
        
        if filename:
            self.file_var.set(filename)
    
    def browse_player(self):
        """Выбор плеера через диалоговое окно"""
        filename = filedialog.askopenfilename(
            title="Выберите плеер",
            filetypes=[("Исполняемые файлы", "*.exe *.app"), ("Все файлы", "*.*")]
        )
        if filename:
            player_name = os.path.basename(filename)
            current_values = list(self.player_combo['values'])
            if player_name not in current_values:
                current_values.append(player_name)
                self.player_combo['values'] = current_values
            self.player_combo.set(player_name)
            self.user_player_path = filename
    
    def add_task(self):
        """Добавление новой задачи"""
        file_path = self.file_var.get().strip()
        if not file_path:
            messagebox.showerror("Ошибка", "Выберите файл!")
            return
        
        if not os.path.exists(file_path):
            messagebox.showerror("Ошибка", "Файл не найден!")
            return
        
        file_type = self.file_type_var.get()
        
        try:
            day = int(self.day_spin.get())
            month = int(self.month_spin.get())
            year = int(self.year_spin.get())
            hour = int(self.hour_spin.get())
            minute = int(self.min_spin.get())
            datetime(year, month, day, hour, minute)
        except ValueError:
            messagebox.showerror("Ошибка", "Некорректная дата или время!")
            return
        
        player = ""
        if file_type != "program":
            player_name = self.player_combo.get()
            if not player_name:
                messagebox.showerror("Ошибка", "Выберите плеер!")
                return
        
        task = {
            "file_path": file_path,
            "file_type": file_type,
            "player": player,
            "year": year,
            "month": month,
            "day": day,
            "hour": hour,
            "minute": minute,
            "enabled": True,
            "last_run": None,
            "task_name": os.path.basename(file_path)
        }
        
        self.tasks.append(task)
        self.update_task_list()
        self.file_var.set("")
        
        messagebox.showinfo("Успех", f"✅ Задача добавлена!\n{task['task_name']}")
    
    def update_task_list(self):
        """Обновление списка задач"""
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        type_names = {
            "program": "Программа",
            "video": "🎬 Видео",
            "audio": "🎵 Аудио"
        }
        
        for i, task in enumerate(self.tasks):
            status = "✅ Активна" if task.get("enabled", True) else "❌ Отключена"
            if task.get("last_run"):
                status += f"\n(посл. {task['last_run']})"
            
            scheduled = datetime(task['year'], task['month'], task['day'], 
                               task['hour'], task['minute'])
            now = datetime.now()
            
            if scheduled < now and not task.get("last_run"):
                status = "⏰ Пропущена"
            elif scheduled < now and task.get("last_run"):
                status = "✅ Выполнена"
            
            date_str = f"{task['day']:02d}.{task['month']:02d}.{task['year']} {task['hour']:02d}:{task['minute']:02d}"
            
            self.tree.insert("", tk.END, values=(
                date_str,
                type_names.get(task.get("file_type", "program"), ""),
                task['task_name'],
                status
            ), tags=(str(i),))
    
    def delete_task(self, index):
        """Удаление задачи"""
        if 0 <= index < len(self.tasks):
            if messagebox.askyesno("Подтверждение", f"Удалить задачу для {self.tasks[index]['task_name']}?"):
                del self.tasks[index]
                self.update_task_list()
                self.save_tasks()
    
    def toggle_task(self, index):
        """Включение/отключение задачи"""
        if 0 <= index < len(self.tasks):
            self.tasks[index]["enabled"] = not self.tasks[index].get("enabled", True)
            self.update_task_list()
            self.save_tasks()
    
    def clear_tasks(self):
        """Очистка всех задач"""
        if self.tasks and messagebox.askyesno("Подтверждение", "Удалить все задачи?"):
            self.tasks = []
            self.update_task_list()
            self.save_tasks()
    
    def start_timer(self):
        """Запуск таймера"""
        if not self.tasks:
            messagebox.showwarning("Предупреждение", "Нет задач!")
            return
        
        active_tasks = [t for t in self.tasks if t.get("enabled", True)]
        if not active_tasks:
            messagebox.showwarning("Предупреждение", "Нет активных задач!")
            return
        
        self.running = True
        self.timer_start_time = datetime.now()
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.status_label.config(text="📊 Статус: Запущен")
        
        if hasattr(self, 'tray_icon'):
            self.tray_icon.title = "⏰ Таймер запущен"
        
        # Показываем уведомление о запуске
        self.show_notification("⏰ Таймер запущен", 
                              f"Таймер успешно запущен!\nАктивных задач: {len(active_tasks)}\nВремя: {datetime.now().strftime('%H:%M:%S')}")
        
        self.thread = threading.Thread(target=self.monitor_tasks, daemon=True)
        self.thread.start()
    
    def stop_timer(self):
        """Остановка таймера"""
        self.running = False
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.status_label.config(text="📊 Статус: Остановлен")
        
        if hasattr(self, 'tray_icon'):
            self.tray_icon.title = "⏰ Таймер остановлен"
        
        # Убираем уведомление об остановке таймера
        # Просто обновляем статус без уведомления
        
        self.timer_start_time = None
    
    def monitor_tasks(self):
        """Мониторинг задач"""
        while self.running:
            now = datetime.now()
            
            for i, task in enumerate(self.tasks):
                if not task.get("enabled", True):
                    continue
                
                if task.get("last_run") == now.strftime("%Y-%m-%d"):
                    continue
                
                scheduled = datetime(task['year'], task['month'], task['day'], 
                                   task['hour'], task['minute'])
                
                if (scheduled.year == now.year and 
                    scheduled.month == now.month and 
                    scheduled.day == now.day and 
                    scheduled.hour == now.hour and 
                    scheduled.minute == now.minute):
                    
                    self.run_task(i, task)
                    task["last_run"] = now.strftime("%Y-%m-%d")
                    self.root.after(0, self.update_task_list)
            
            time.sleep(30)
    
    def run_task(self, index, task):
        """Запуск задачи"""
        try:
            file_path = task['file_path']
            file_type = task.get('file_type', 'program')
            
            if file_type == "program":
                subprocess.Popen([file_path], shell=True)
            else:
                player = task.get('player', '')
                if player:
                    subprocess.Popen([player, file_path], shell=False)
                else:
                    subprocess.Popen([file_path], shell=True)
            
            # Убираем уведомление о запуске задачи
            # Просто обновляем статус в интерфейсе
            task_name = os.path.basename(file_path)
            self.root.after(0, lambda: self.status_label.config(
                text=f"📊 Статус: Запущен {task_name}"
            ))
            
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Ошибка запуска", str(e)))
    
    def show_notification(self, title, message):
        """Показ уведомления"""
        try:
            # Пытаемся использовать системное уведомление
            if platform.system() == "Windows":
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
            elif platform.system() == "Darwin":  # macOS
                subprocess.run(['osascript', '-e', f'display notification "{message}" with title "{title}"'])
            else:  # Linux
                try:
                    subprocess.run(['notify-send', title, message])
                except:
                    self.show_tk_notification(title, message)
        except:
            self.show_tk_notification(title, message)
    
    def show_tk_notification(self, title, message):
        """Показ уведомления в Tkinter"""
        # Закрываем предыдущее уведомление
        if hasattr(self, 'notification_window') and self.notification_window:
            try:
                self.notification_window.destroy()
            except:
                pass
        
        # Создаем новое уведомление
        self.notification_window = tk.Toplevel(self.root)
        self.notification_window.title(title)
        self.notification_window.geometry("350x150")
        self.notification_window.resizable(False, False)
        
        # Размещаем окно в центре
        self.notification_window.transient(self.root)
        self.notification_window.grab_set()
        
        # Фрейм для содержимого
        frame = ttk.Frame(self.notification_window, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        
        # Иконка
        icon_label = ttk.Label(frame, text="🔔", font=('Arial', 30))
        icon_label.pack(pady=(0, 10))
        
        # Заголовок
        ttk.Label(frame, text=title, font=('Arial', 12, 'bold')).pack(pady=(0, 5))
        
        # Сообщение
        ttk.Label(frame, text=message, font=('Arial', 10)).pack(pady=(0, 10))
        
        # Кнопка закрытия
        ttk.Button(frame, text="OK", command=self.notification_window.destroy).pack()
        
        # Автоматическое закрытие через 5 секунд
        self.root.after(5000, self.close_notification)
    
    def close_notification(self):
        """Закрытие окна уведомления"""
        if hasattr(self, 'notification_window') and self.notification_window:
            try:
                self.notification_window.destroy()
                self.notification_window = None
            except:
                pass
    
    def show_timer_status(self):
        """Показ статуса таймера"""
        if self.running:
            if self.timer_start_time:
                duration = datetime.now() - self.timer_start_time
                hours = duration.seconds // 3600
                minutes = (duration.seconds % 3600) // 60
                seconds = duration.seconds % 60
                time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                
                active_tasks = [t for t in self.tasks if t.get("enabled", True)]
                executed_tasks = [t for t in self.tasks if t.get("last_run") == datetime.now().strftime("%Y-%m-%d")]
                
                status_msg = (f"⏰ Таймер активен\n"
                             f"Время работы: {time_str}\n"
                             f"Всего задач: {len(self.tasks)}\n"
                             f"Активных задач: {len(active_tasks)}\n"
                             f"Выполнено сегодня: {len(executed_tasks)}")
                
                self.show_notification("📊 Статус таймера", status_msg)
            else:
                self.show_notification("📊 Статус таймера", "Таймер запущен, но время не зафиксировано")
        else:
            self.show_notification("📊 Статус таймера", "⏹ Таймер остановлен")
    
    def show_tasks_notification(self, icon=None, item=None):
        """Показ списка задач в уведомлении"""
        if not self.tasks:
            self.show_notification("📋 Список задач", "Нет активных задач")
            return
        
        active_tasks = [t for t in self.tasks if t.get("enabled", True)]
        task_list = "📋 Активные задачи:\n\n"
        for i, task in enumerate(active_tasks[:5]):  # Показываем первые 5
            task_list += f"{i+1}. {task['task_name']}\n"
            task_list += f"   ⏰ {task['day']:02d}.{task['month']:02d}.{task['year']} {task['hour']:02d}:{task['minute']:02d}\n"
        
        if len(active_tasks) > 5:
            task_list += f"\n... и еще {len(active_tasks) - 5} задач"
        
        self.show_notification(f"📋 Список задач ({len(active_tasks)} активных)", task_list)
    
    def save_tasks(self):
        """Сохранение задач"""
        try:
            with open("timer_tasks.json", "w", encoding="utf-8") as f:
                json.dump({"tasks": self.tasks}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить: {e}")
    
    def load_tasks(self):
        """Загрузка задач"""
        try:
            if os.path.exists("timer_tasks.json"):
                with open("timer_tasks.json", "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.tasks = data.get("tasks", [])
        except:
            self.tasks = []
    
    def load_tasks_from_file(self):
        """Загрузка из файла"""
        filename = filedialog.askopenfilename(
            title="Выберите файл",
            filetypes=[("JSON файлы", "*.json")]
        )
        if filename:
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.tasks = data.get("tasks", [])
                    self.update_task_list()
                    messagebox.showinfo("Успех", f"✅ Загружено {len(self.tasks)} задач")
            except Exception as e:
                messagebox.showerror("Ошибка", str(e))
    
    def hide_window(self):
        """Сворачивание в трей"""
        self.root.withdraw()
        self.hidden = True
        if hasattr(self, 'tray_icon'):
            self.tray_icon.visible = True
    
    def show_window(self, icon=None, item=None):
        """Показ окна из трея"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.hidden = False
        if hasattr(self, 'tray_icon'):
            self.tray_icon.visible = False
    
    def start_timer_from_tray(self, icon=None, item=None):
        """Запуск таймера из трея"""
        self.show_window()
        self.start_timer()
    
    def stop_timer_from_tray(self, icon=None, item=None):
        """Остановка таймера из трея"""
        self.stop_timer()
    
    def quit_app(self, icon=None, item=None):
        """Выход из приложения"""
        if messagebox.askyesno("Выход", "Вы уверены, что хотите выйти?"):
            self.stop_timer()
            self.save_tasks()
            if hasattr(self, 'tray_icon'):
                self.tray_icon.stop()
            self.root.quit()
            self.root.destroy()
            sys.exit()
    
    def on_closing(self):
        """Обработка закрытия окна"""
        self.hide_window()

if __name__ == "__main__":
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("=" * 50)
        print("Для работы программы в фоновом режиме установите:")
        print("pip install pystray pillow")
        print("=" * 50)
    
    root = tk.Tk()
    app = TimerApp(root)
    root.mainloop()