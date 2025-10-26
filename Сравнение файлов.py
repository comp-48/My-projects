import os
import difflib
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, scrolledtext

class FileComparator:
    def __init__(self, root):
        self.root = root
        self.root.title("Сравнение файлов")
        self.root.geometry("900x600")
        self.root.minsize(800, 500)
        
        self.create_widgets()
        self.setup_layout()
        self.setup_context_menus()
        
    def create_widgets(self):
        # Фрейм для выбора файлов
        self.file_frame = tk.LabelFrame(self.root, text="Выбор файлов")
        self.file_frame.pack(fill="x", padx=10, pady=5)
        
        # Поле для первого файла
        tk.Label(self.file_frame, text="Файл 1:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.file1_entry = tk.Entry(self.file_frame, width=60)
        self.file1_entry.grid(row=0, column=1, padx=5, pady=5, sticky="we")
        tk.Button(self.file_frame, text="Обзор...", command=lambda: self.browse_file(self.file1_entry)).grid(row=0, column=2, padx=5, pady=5)
        
        # Поле для второго файла
        tk.Label(self.file_frame, text="Файл 2:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        self.file2_entry = tk.Entry(self.file_frame, width=60)
        self.file2_entry.grid(row=1, column=1, padx=5, pady=5, sticky="we")
        tk.Button(self.file_frame, text="Обзор...", command=lambda: self.browse_file(self.file2_entry)).grid(row=1, column=2, padx=5, pady=5)
        
        # Фрейм для настроек
        self.settings_frame = tk.LabelFrame(self.root, text="Настройки сравнения")
        self.settings_frame.pack(fill="x", padx=10, pady=5)
        
        # Режим сравнения
        tk.Label(self.settings_frame, text="Режим:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.mode_var = tk.StringVar(value="text")
        modes = [("Текстовый (сравнивает содержимое)", "text"), 
                 ("Бинарный (побайтовое сравнение)", "binary")]
        for i, (text, mode) in enumerate(modes):
            tk.Radiobutton(self.settings_frame, text=text, variable=self.mode_var, 
                          value=mode).grid(row=0, column=i+1, padx=5, pady=5, sticky="w")
        
        # Кнопка сравнения
        self.compare_btn = tk.Button(self.root, text="Сравнить файлы", command=self.compare_files, 
                                    bg="#4CAF50", fg="white", font=("Arial", 10, "bold"))
        self.compare_btn.pack(pady=10)
        
        # Панель с вкладками для результатов
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=5)
        
        # Вкладка для текстового сравнения
        self.text_tab = tk.Frame(self.notebook)
        self.notebook.add(self.text_tab, text="Текстовое сравнение")
        
        # Текстовые виджеты с прокруткой
        self.text_frame = tk.Frame(self.text_tab)
        self.text_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Прокрутка
        self.scroll_y = tk.Scrollbar(self.text_frame, orient="vertical")
        self.scroll_y.pack(side="right", fill="y")
        
        self.text1 = tk.Text(self.text_frame, wrap="none", yscrollcommand=self.scroll_y.set, 
                           bg="#f0f0f0", font=("Courier New", 10))
        self.text1.pack(side="left", fill="both", expand=True)
        
        self.text2 = tk.Text(self.text_frame, wrap="none", yscrollcommand=self.scroll_y.set, 
                           bg="#f0f0f0", font=("Courier New", 10))
        self.text2.pack(side="left", fill="both", expand=True)
        
        self.scroll_y.config(command=self.sync_scroll)
        
        # Вкладка для бинарного сравнения
        self.binary_tab = tk.Frame(self.notebook)
        self.notebook.add(self.binary_tab, text="Бинарное сравнение")
        
        self.binary_result = scrolledtext.ScrolledText(self.binary_tab, wrap="word", 
                                                     font=("Arial", 10), bg="#f0f0f0")
        self.binary_result.pack(fill="both", expand=True, padx=5, pady=5)
        self.binary_result.config(state="disabled")
        
        # Статус бар
        self.status_var = tk.StringVar()
        self.status_bar = tk.Label(self.root, textvariable=self.status_var, bd=1, relief="sunken", anchor="w")
        self.status_bar.pack(side="bottom", fill="x")
        
        # Настройка тегов для подсветки
        self.text1.tag_configure("diff", background="#ffcccc")
        self.text2.tag_configure("diff", background="#ffcccc")
        self.text1.tag_configure("header", background="#e0e0e0", font=("Arial", 9, "bold"))
        self.text2.tag_configure("header", background="#e0e0e0", font=("Arial", 9, "bold"))
    
    def setup_layout(self):
        self.file_frame.columnconfigure(1, weight=1)
        self.settings_frame.columnconfigure(1, weight=1)
    
    def browse_file(self, entry_widget):
        file_path = filedialog.askopenfilename()
        if file_path:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, file_path)
    
    def sync_scroll(self, *args):
        self.text1.yview(*args)
        self.text2.yview(*args)
    
    def compare_files(self):
        file1 = self.file1_entry.get()
        file2 = self.file2_entry.get()
        mode = self.mode_var.get()
        
        if not file1 or not file2:
            messagebox.showerror("Ошибка", "Укажите оба файла для сравнения")
            return
        
        if not os.path.exists(file1) or not os.path.exists(file2):
            messagebox.showerror("Ошибка", "Один из файлов не существует")
            return
        
        try:
            if mode == "text":
                self.compare_text_files(file1, file2)
                self.notebook.select(self.text_tab)
            else:
                self.compare_binary_files(file1, file2)
                self.notebook.select(self.binary_tab)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Произошла ошибка: {str(e)}")
    
    def compare_text_files(self, file1, file2):
        # Очистка предыдущих результатов
        self.text1.config(state="normal")
        self.text2.config(state="normal")
        self.text1.delete(1.0, tk.END)
        self.text2.delete(1.0, tk.END)
        
        # Заголовки
        self.text1.insert(tk.END, f"Файл 1: {os.path.basename(file1)}\n", "header")
        self.text2.insert(tk.END, f"Файл 2: {os.path.basename(file2)}\n", "header")
        
        # Чтение файлов
        try:
            with open(file1, "r", encoding="utf-8") as f1:
                lines1 = f1.readlines()
            with open(file2, "r", encoding="utf-8") as f2:
                lines2 = f2.readlines()
        except UnicodeDecodeError:
            with open(file1, "r", encoding="latin-1") as f1:
                lines1 = f1.readlines()
            with open(file2, "r", encoding="latin-1") as f2:
                lines2 = f2.readlines()
        
        # Сравнение
        diff = difflib.SequenceMatcher(None, lines1, lines2)
        diff_count = 0
        
        for opcode in diff.get_opcodes():
            tag = opcode[0]
            i1, i2, j1, j2 = opcode[1], opcode[2], opcode[3], opcode[4]
            
            if tag == "equal":
                # Совпадающие строки
                for line in lines1[i1:i2]:
                    self.text1.insert(tk.END, line)
                for line in lines2[j1:j2]:
                    self.text2.insert(tk.END, line)
            else:
                # Различающиеся строки
                diff_count += 1
                # Для файла 1
                for line in lines1[i1:i2]:
                    self.text1.insert(tk.END, line, "diff")
                # Для файла 2
                for line in lines2[j1:j2]:
                    self.text2.insert(tk.END, line, "diff")
                
                # Добавляем разделитель
                sep = "-" * 80 + "\n"
                self.text1.insert(tk.END, sep, "diff")
                self.text2.insert(tk.END, sep, "diff")
        
        # Статус
        if diff_count == 0:
            self.status_var.set("Файлы идентичны")
            self.text1.insert(tk.END, "\n\nФайлы идентичны", "header")
            self.text2.insert(tk.END, "\n\nФайлы идентичны", "header")
        else:
            self.status_var.set(f"Найдено различий: {diff_count}")
        
        # Блокировка редактирования
        self.text1.config(state="disabled")
        self.text2.config(state="disabled")
    
    def compare_binary_files(self, file1, file2):
        self.binary_result.config(state="normal")
        self.binary_result.delete(1.0, tk.END)
        
        size1 = os.path.getsize(file1)
        size2 = os.path.getsize(file2)
        
        result = []
        result.append(f"Файл 1: {os.path.basename(file1)} ({size1} байт)")
        result.append(f"Файл 2: {os.path.basename(file2)} ({size2} байт)\n")
        
        if size1 != size2:
            result.append("Файлы имеют разный размер!")
            result.append(f"Разница: {abs(size1 - size2)} байт\n")
        
        # Быстрая проверка по размеру
        if size1 != size2:
            result.append("Файлы различны (проверка по размеру)")
            self.binary_result.insert(tk.END, "\n".join(result))
            self.status_var.set("Файлы имеют разный размер")
            self.binary_result.config(state="disabled")
            return
        
        # Побайтовое сравнение
        block_size = 4096
        pos = 0
        found_diff = False
        
        with open(file1, "rb") as f1, open(file2, "rb") as f2:
            while True:
                block1 = f1.read(block_size)
                block2 = f2.read(block_size)
                
                if not block1 and not block2:
                    break
                
                if block1 != block2:
                    # Поиск точной позиции различия
                    for i in range(min(len(block1), len(block2))):
                        if block1[i] != block2[i]:
                            result.append(f"Первое различие на позиции: {pos + i}")
                            result.append(f"Байт файла 1: {block1[i]}")
                            result.append(f"Байт файла 2: {block2[i]}")
                            found_diff = True
                            break
                    if found_diff:
                        break
                pos += len(block1)
        
        if not found_diff:
            result.append("Файлы идентичны (бинарное сравнение)")
            self.status_var.set("Файлы идентичны")
        else:
            result.append("\nФайлы различны")
            self.status_var.set("Файлы различаются")
        
        self.binary_result.insert(tk.END, "\n".join(result))
        self.binary_result.config(state="disabled")
    
    # Функции для работы с контекстным меню
    def setup_context_menus(self):
        # Контекстное меню для первого текстового виджета
        self.menu1 = tk.Menu(self.text1, tearoff=0)
        self.menu1.add_command(label="Копировать", command=lambda: self.copy_text(self.text1))
        self.text1.bind("<Button-3>", lambda e: self.show_menu(e, self.menu1))
        
        # Контекстное меню для второго текстового виджета
        self.menu2 = tk.Menu(self.text2, tearoff=0)
        self.menu2.add_command(label="Копировать", command=lambda: self.copy_text(self.text2))
        self.text2.bind("<Button-3>", lambda e: self.show_menu(e, self.menu2))
        
        # Контекстное меню для бинарного сравнения
        self.binary_menu = tk.Menu(self.binary_result, tearoff=0)
        self.binary_menu.add_command(label="Копировать", command=lambda: self.copy_text(self.binary_result))
        self.binary_menu.add_command(label="Копировать все", command=lambda: self.copy_all(self.binary_result))
        self.binary_result.bind("<Button-3>", lambda e: self.show_menu(e, self.binary_menu))
    
    def show_menu(self, event, menu):
        try:
            # Выделяем текст под курсором для удобства
            event.widget.tag_remove("sel", "1.0", tk.END)
            event.widget.tag_add("sel", "current linestart", "current lineend")
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    
    def copy_text(self, text_widget):
        try:
            if text_widget.cget('state') == tk.DISABLED:
                text_widget.config(state=tk.NORMAL)
                selected = text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                text_widget.clipboard_clear()
                text_widget.clipboard_append(selected)
                text_widget.config(state=tk.DISABLED)
            else:
                selected = text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                text_widget.clipboard_clear()
                text_widget.clipboard_append(selected)
        except tk.TclError:
            pass  # Если ничего не выделено
    
    def copy_all(self, text_widget):
        if text_widget.cget('state') == tk.DISABLED:
            text_widget.config(state=tk.NORMAL)
            content = text_widget.get(1.0, tk.END)
            text_widget.clipboard_clear()
            text_widget.clipboard_append(content)
            text_widget.config(state=tk.DISABLED)
        else:
            content = text_widget.get(1.0, tk.END)
            text_widget.clipboard_clear()
            text_widget.clipboard_append(content)

if __name__ == "__main__":
    root = tk.Tk()
    app = FileComparator(root)
    root.mainloop()
