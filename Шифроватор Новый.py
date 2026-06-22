import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import base64
import os
import threading
import string
import platform

class CryptoApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Криптографическая программа")
        self.root.geometry("800x700")
        
        # Инициализация ключей
        self.public_key = None
        self.private_key = None
        
        # Создаем вкладки
        self.tab_control = ttk.Notebook(root)
        
        self.symmetric_tab = ttk.Frame(self.tab_control)
        self.asymmetric_tab = ttk.Frame(self.tab_control)
        self.file_tab = ttk.Frame(self.tab_control)
        self.about_tab = ttk.Frame(self.tab_control)
        
        self.tab_control.add(self.symmetric_tab, text='Симметричное (AES)')
        self.tab_control.add(self.asymmetric_tab, text='Асимметричное (RSA)')
        self.tab_control.add(self.file_tab, text='Шифрование файлов')
        self.tab_control.add(self.about_tab, text='О программе')
        
        self.tab_control.pack(expand=1, fill='both')
        
        # Инициализация вкладок
        self.setup_symmetric_tab()
        self.setup_asymmetric_tab()
        self.setup_file_tab()
        self.setup_about_tab()
    
    def setup_symmetric_tab(self):
        # Поле ввода текста с кнопками
        text_frame = ttk.Frame(self.symmetric_tab)
        text_frame.pack(pady=5, fill=tk.X, padx=10)
        
        ttk.Label(text_frame, text="Текст:").pack(side=tk.LEFT)
        ttk.Button(text_frame, text="Вставить", command=self.paste_text).pack(side=tk.RIGHT, padx=5)
        
        self.sym_text = tk.Text(self.symmetric_tab, height=5)
        self.sym_text.pack(pady=5, fill=tk.X, padx=10)
        
        # Пароль с кнопками копирования/вставки
        password_frame = ttk.Frame(self.symmetric_tab)
        password_frame.pack(pady=5, fill=tk.X, padx=10)
        
        ttk.Label(password_frame, text="Пароль:").pack(side=tk.LEFT)
        self.password_entry = ttk.Entry(password_frame, show="*")
        self.password_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        # Кнопки для работы с паролем
        ttk.Button(password_frame, text="📋", width=3, command=self.copy_password).pack(side=tk.LEFT, padx=2)
        ttk.Button(password_frame, text="📄", width=3, command=self.paste_password).pack(side=tk.LEFT, padx=2)
        
        # Кнопки операций
        btn_frame = ttk.Frame(self.symmetric_tab)
        btn_frame.pack(pady=10)
        
        ttk.Button(btn_frame, text="Зашифровать", command=self.encrypt_symmetric).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Расшифровать", command=self.decrypt_symmetric).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Очистить", command=self.clear_symmetric).pack(side=tk.LEFT, padx=5)
        
        # Результат с кнопками копирования
        result_frame = ttk.Frame(self.symmetric_tab)
        result_frame.pack(pady=5, fill=tk.X, padx=10)
        
        ttk.Label(result_frame, text="Результат:").pack(side=tk.LEFT)
        ttk.Button(result_frame, text="Копировать", command=self.copy_symmetric_result).pack(side=tk.RIGHT, padx=5)
        
        self.sym_result = tk.Text(self.symmetric_tab, height=5)
        self.sym_result.pack(pady=5, fill=tk.BOTH, expand=True, padx=10)
    
    def setup_asymmetric_tab(self):
        # Генерация ключей
        key_frame = ttk.LabelFrame(self.asymmetric_tab, text="Ключи")
        key_frame.pack(pady=5, fill=tk.X, padx=10)
        
        ttk.Button(key_frame, text="Сгенерировать ключи", command=self.generate_keys).pack(pady=5, side=tk.LEFT, padx=5)
        
        # Кнопки загрузки ключей
        ttk.Button(key_frame, text="Загрузить публичный ключ", command=self.load_public_key).pack(pady=5, side=tk.LEFT, padx=5)
        ttk.Button(key_frame, text="Загрузить приватный ключ", command=self.load_private_key).pack(pady=5, side=tk.LEFT, padx=5)
        
        # Поле ввода текста с кнопкой вставки
        text_input_frame = ttk.Frame(self.asymmetric_tab)
        text_input_frame.pack(pady=5, fill=tk.X, padx=10)
        
        ttk.Label(text_input_frame, text="Текст:").pack(side=tk.LEFT)
        ttk.Button(text_input_frame, text="Вставить", command=self.paste_asym_text).pack(side=tk.RIGHT, padx=5)
        
        self.asym_text = tk.Text(self.asymmetric_tab, height=5)
        self.asym_text.pack(pady=5, fill=tk.X, padx=10)
        
        # Кнопки операций для текста
        text_btn_frame = ttk.Frame(self.asymmetric_tab)
        text_btn_frame.pack(pady=10)
        
        ttk.Button(text_btn_frame, text="Зашифровать текст", command=self.encrypt_asymmetric).pack(side=tk.LEFT, padx=5)
        ttk.Button(text_btn_frame, text="Расшифровать текст", command=self.decrypt_asymmetric).pack(side=tk.LEFT, padx=5)
        ttk.Button(text_btn_frame, text="Очистить", command=self.clear_asymmetric).pack(side=tk.LEFT, padx=5)
        
        # Результат с кнопкой копирования
        result_frame = ttk.Frame(self.asymmetric_tab)
        result_frame.pack(pady=5, fill=tk.X, padx=10)
        
        ttk.Label(result_frame, text="Результат:").pack(side=tk.LEFT)
        ttk.Button(result_frame, text="Копировать", command=self.copy_asymmetric_result).pack(side=tk.RIGHT, padx=5)
        
        self.asym_result = tk.Text(self.asymmetric_tab, height=5)
        self.asym_result.pack(pady=5, fill=tk.BOTH, expand=True, padx=10)
        
        # Раздел: Шифрование файлов и папок
        file_frame = ttk.LabelFrame(self.asymmetric_tab, text="Шифрование файлов и папок")
        file_frame.pack(pady=10, fill=tk.X, padx=10)
        
        # Выбор папки или диска для шифрования
        encrypt_path_frame = ttk.Frame(file_frame)
        encrypt_path_frame.pack(pady=5, fill=tk.X, padx=5)
        
        ttk.Label(encrypt_path_frame, text="Шифровать:").pack(side=tk.LEFT)
        self.encrypt_path = tk.StringVar()
        ttk.Entry(encrypt_path_frame, textvariable=self.encrypt_path, state='readonly').pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(encrypt_path_frame, text="Папка", command=lambda: self.select_path("folder", "encrypt")).pack(side=tk.LEFT, padx=2)
        
        # Для Windows добавляем кнопку выбора диска
        if platform.system() == "Windows":
            ttk.Button(encrypt_path_frame, text="Диск", command=lambda: self.select_path("drive", "encrypt")).pack(side=tk.LEFT, padx=2)
        
        # Выбор папки или диска для расшифровки
        decrypt_path_frame = ttk.Frame(file_frame)
        decrypt_path_frame.pack(pady=5, fill=tk.X, padx=5)
        
        ttk.Label(decrypt_path_frame, text="Расшифровать:").pack(side=tk.LEFT)
        self.decrypt_path = tk.StringVar()
        ttk.Entry(decrypt_path_frame, textvariable=self.decrypt_path, state='readonly').pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(decrypt_path_frame, text="Папка", command=lambda: self.select_path("folder", "decrypt")).pack(side=tk.LEFT, padx=2)
        
        # Для Windows добавляем кнопку выбора диска
        if platform.system() == "Windows":
            ttk.Button(decrypt_path_frame, text="Диск", command=lambda: self.select_path("drive", "decrypt")).pack(side=tk.LEFT, padx=2)
        
        # Расширения файлов
        ttk.Label(file_frame, text="Расширения файлов (через запятую):").pack(pady=5)
        self.asym_extensions = ttk.Entry(file_frame)
        # ОБНОВЛЕННЫЙ СПИСОК ФОРМАТОВ
        extensions_str = "docx,xls,xlsx,ppt,pptx,pdf,txt,rtf,odt,ods,odp,mp3,wav,flac,aac,ogg,wma,m4a,avi,mov,mkv,flv,wmv,webm,mpeg,mpg,zip,rar,7z,tar,gz,exe,msi,dmg,apk,app,bat,sh,html,htm,css,js,php,xml,json,csv,sql,db,sqlite,dat,log,ini,cfg,dll,sys,iso,raw,mdb,accdb,mp4,jpeg,png,tiff,dt,lic,1cd,scv,gif,bmp,svg,webp,ico,psd,ai,eps,heic,heif,aiff,aif,opus,mid,midi,ac3,m4v,3gp,vob,ts,m2ts,ogv,rm,rmvb,asf,md,tex,epub,pages,numbers,key,djvu,yaml,yml,bz2,xz,cab,jar,war,ear,img,cmd,ps1,py,vbs,com,so,dylib,xhtml,jsx,vue,less,sass,scss,asp,aspx,jsp,sqlite3,dbf,frm,myd,ndf,mdf,ldf,ora,pdb,inf,reg,bak,old,lock,tmp,vcf,ics,eml,pst,ost,torrent,cue,sub,srt,stl,blend,fbx,obj,step,iges,3mf,amf,der,pfx,key,crt,csr,p12,pem,sxw,stw,3ds,max,3dm,sxc,stc,dif,slk,wb2,sxd,std,sxm,sqlitedb,accdb,odb,cpp,pas,asm,sch,class,swf,fla,m3u,m4u,tif,backup,tgz,ARC,vmdk,vdi,sldm,sldx,sti,sxi,dwg,wk1,wks,msg,ppsx,ppsm,pps,pot,pptm,xltm,xltx,xlc,xlm,xlt,xlw,xlsb,dotm,dot,docm,deb,skp,pln,pla,bpn,h264,h265,mjpeg,bck,arw,nef,dds,mobi,jpg,ispy,xspf,Config,chm,cer,gpx,UCIP,bin,doc,rdp,fb2,jfif,wfw,nm7,oldlic,vkey,azw,azw3,ibooks,fb3,cbr,cbz,cb7,sty,cls,bib,nb,ipynb,hlp,notes,enex,one,abbyy,ocr,cr2,cr3,nrw,srw,raf,orf,rw2,dng,exr,hdr,pcx,tga,jxr,bpg,cdr,fig,wmf,emf,sketch,xd,figma,stp,igs,gltf,glb,usd,usdz,ply,vox,mdl,md2,md3,md5,ttf,otf,woff,woff2,fon,aup,aup3,als,flp,cpr,rpp,ptx,logicx,vst,vst3,au,aax,sf2,sfz,mod,xm,it,s3m,ape,wv,tta,caf,aa,m2t,divx,vc1,prores,dnxhd,dnxhr,aep,prproj,fcpxml,fcpx,vegas,drp,ass,ssa,vtt,smi,pjs,nrg,mds,vcd,dvdmedia,ace,arj,lha,lzh,z,zz,zst,lz,lzma,zipx,par,par2,sfx,scr,cpl,msc,run,ko,elf,jspf,java,lua,pl,rb,r,psm1,swift,kt,go,rs,pol,evt,evtx,ibd,nsf,fdb,gdb,odb,4dd,kml,kmz,shp,shx,geojson,gml,vhd,vhdx,qcow2,ova,ovf,dmp,core,hprof,wasm,htaccess,ejs,pug,mdx,rst,graphql,proto,avro,thrift,toml,properties,env,npmrc,babelrc,eslintrc,cbt,gpg,pgp,axx,magnet,license,wallet,sfap0,asnd,nki,nkm,exs,gig,pcast,8svx,smp,sndt,sndr,dtshd,mxf,lrv,thp,bik,piv,r3d,braw,cin,asm_cad,prt,ipt,iam,wire,lwo,lws,ma,mb,hip,bip,x_b,x_t,jt,sat,neu,sch_eda,brd,gbr,drl,lz4,zstd,br,lha_unix,cpio,sparseimage,sparsebundle,wim,sqfs,squashfs,cif,uif,ccd,mdf_alcohol,toast,vc4,vc6,vdi_virtualbox,vpc,pdb_palmdoc,prc_ebook,lrf,lrx,lrs,opf,ncx,tr3,dsl,xps,oxps,msp,msu,wsf,vbe,jse,psc1,psd1,rpm,pkg_tar_zst,aab,appx,msix,pfb,pfm,dfont,suit,bdf,pcf,tab_mapinfo,mid_mapinfo,mif,gdb_esri,dem,ecw,jks,keystore,ppk,asc,bkf,edb,olk14Message,olk14Category,sav_spss,ibdata,vix"
        self.asym_extensions.insert(0, extensions_str)
        self.asym_extensions.pack(pady=5, fill=tk.X, padx=10)
        
        # Опции
        options_frame = ttk.Frame(file_frame)
        options_frame.pack(pady=10, fill=tk.X, padx=10)
        
        self.asym_recursive = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Рекурсивный обход", variable=self.asym_recursive).pack(side=tk.LEFT, padx=5)
        
        self.asym_delete_original = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="Удалить исходные файлы", variable=self.asym_delete_original).pack(side=tk.LEFT, padx=5)
        
        # Кнопки для файлов
        file_btn_frame = ttk.Frame(file_frame)
        file_btn_frame.pack(pady=10)
        
        ttk.Button(file_btn_frame, text="Зашифровать", command=self.encrypt_asym_folder).pack(side=tk.LEFT, padx=5)
        ttk.Button(file_btn_frame, text="Расшифровать", command=self.decrypt_asym_folder).pack(side=tk.LEFT, padx=5)
        
        # Лог операций с прокруткой
        log_frame = ttk.Frame(file_frame)
        log_frame.pack(pady=5, fill=tk.BOTH, expand=True)
        
        ttk.Label(log_frame, text="Лог операций:").pack(anchor=tk.W)
        
        # Создаем фрейм для лога и скроллбара
        log_container = ttk.Frame(log_frame)
        log_container.pack(fill=tk.BOTH, expand=True)
        
        # Добавляем скроллбар
        scrollbar = ttk.Scrollbar(log_container)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.asym_log = tk.Text(log_container, height=8, yscrollcommand=scrollbar.set)
        self.asym_log.pack(pady=5, fill=tk.BOTH, expand=True, padx=10)
        self.asym_log.config(state=tk.DISABLED)
        
        scrollbar.config(command=self.asym_log.yview)
        
        # Прогресс-бар с отображением процентов
        progress_frame = ttk.Frame(file_frame)
        progress_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Метка для отображения процентов
        self.asym_progress_var = tk.StringVar(value="Прогресс: 0%")
        ttk.Label(progress_frame, textvariable=self.asym_progress_var).pack(anchor=tk.W)
        
        # Прогресс-бар
        self.asym_progress = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.asym_progress.pack(fill=tk.X, pady=2)
        
        # Метка для отображения текущего файла
        self.current_file_var = tk.StringVar(value="Текущий файл: ")
        ttk.Label(progress_frame, textvariable=self.current_file_var, anchor=tk.W).pack(fill=tk.X, pady=2)
    
    def setup_file_tab(self):
        # Выбор папки
        folder_frame = ttk.Frame(self.file_tab)
        folder_frame.pack(pady=10, fill=tk.X, padx=10)
        
        ttk.Label(folder_frame, text="Выберите папку:").pack(side=tk.LEFT)
        self.folder_path = tk.StringVar()
        ttk.Entry(folder_frame, textvariable=self.folder_path, state='readonly').pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(folder_frame, text="Обзор", command=self.select_folder).pack(side=tk.LEFT)
        
        # Пароль для файлов
        ttk.Label(self.file_tab, text="Пароль:").pack(pady=5)
        self.file_password = ttk.Entry(self.file_tab, show="*")
        self.file_password.pack(pady=5, fill=tk.X, padx=10)
        
        # Расширения файлов (полный список)
        ttk.Label(self.file_tab, text="Расширения файлов (через запятую):").pack(pady=5)
        self.file_extensions = ttk.Entry(self.file_tab)
        # ОБНОВЛЕННЫЙ СПИСОК ФОРМАТОВ
        extensions_str = "docx,xls,xlsx,ppt,pptx,pdf,txt,rtf,odt,ods,odp,mp3,wav,flac,aac,ogg,wma,m4a,avi,mov,mkv,flv,wmv,webm,mpeg,mpg,zip,rar,7z,tar,gz,exe,msi,dmg,apk,app,bat,sh,html,htm,css,js,php,xml,json,csv,sql,db,sqlite,dat,log,ini,cfg,dll,sys,iso,raw,mdb,accdb,mp4,jpeg,png,tiff,dt,lic,1cd,scv,gif,bmp,svg,webp,ico,psd,ai,eps,heic,heif,aiff,aif,opus,mid,midi,ac3,m4v,3gp,vob,ts,m2ts,ogv,rm,rmvb,asf,md,tex,epub,pages,numbers,key,djvu,yaml,yml,bz2,xz,cab,jar,war,ear,img,cmd,ps1,py,vbs,com,so,dylib,xhtml,jsx,vue,less,sass,scss,asp,aspx,jsp,sqlite3,dbf,frm,myd,ndf,mdf,ldf,ora,pdb,inf,reg,bak,old,lock,tmp,vcf,ics,eml,pst,ost,torrent,cue,sub,srt,stl,blend,fbx,obj,step,iges,3mf,amf,der,pfx,key,crt,csr,p12,pem,sxw,stw,3ds,max,3dm,sxc,stc,dif,slk,wb2,sxd,std,sxm,sqlitedb,accdb,odb,cpp,pas,asm,sch,class,swf,fla,m3u,m4u,tif,backup,tgz,ARC,vmdk,vdi,sldm,sldx,sti,sxi,dwg,wk1,wks,msg,ppsx,ppsm,pps,pot,pptm,xltm,xltx,xlc,xlm,xlt,xlw,xlsb,dotm,dot,docm,deb,skp,pln,pla,bpn,h264,h265,mjpeg,bck,arw,nef,dds,mobi,jpg,ispy,xspf,Config,chm,cer,gpx,UCIP,bin,doc,rdp,fb2,jfif,wfw,nm7,oldlic,vkey,azw,azw3,ibooks,fb3,cbr,cbz,cb7,sty,cls,bib,nb,ipynb,hlp,notes,enex,one,abbyy,ocr,cr2,cr3,nrw,srw,raf,orf,rw2,dng,exr,hdr,pcx,tga,jxr,bpg,cdr,fig,wmf,emf,sketch,xd,figma,stp,igs,gltf,glb,usd,usdz,ply,vox,mdl,md2,md3,md5,ttf,otf,woff,woff2,fon,aup,aup3,als,flp,cpr,rpp,ptx,logicx,vst,vst3,au,aax,sf2,sfz,mod,xm,it,s3m,ape,wv,tta,caf,aa,m2t,divx,vc1,prores,dnxhd,dnxhr,aep,prproj,fcpxml,fcpx,vegas,drp,ass,ssa,vtt,smi,pjs,nrg,mds,vcd,dvdmedia,ace,arj,lha,lzh,z,zz,zst,lz,lzma,zipx,par,par2,sfx,scr,cpl,msc,run,ko,elf,jspf,java,lua,pl,rb,r,psm1,swift,kt,go,rs,pol,evt,evtx,ibd,nsf,fdb,gdb,odb,4dd,kml,kmz,shp,shx,geojson,gml,vhd,vhdx,qcow2,ova,ovf,dmp,core,hprof,wasm,htaccess,ejs,pug,mdx,rst,graphql,proto,avro,thrift,toml,properties,env,npmrc,babelrc,eslintrc,cbt,gpg,pgp,axx,magnet,license,wallet,sfap0,asnd,nki,nkm,exs,gig,pcast,8svx,smp,sndt,sndr,dtshd,mxf,lrv,thp,bik,piv,r3d,braw,cin,asm_cad,prt,ipt,iam,wire,lwo,lws,ma,mb,hip,bip,x_b,x_t,jt,sat,neu,sch_eda,brd,gbr,drl,lz4,zstd,br,lha_unix,cpio,sparseimage,sparsebundle,wim,sqfs,squashfs,cif,uif,ccd,mdf_alcohol,toast,vc4,vc6,vdi_virtualbox,vpc,pdb_palmdoc,prc_ebook,lrf,lrx,lrs,opf,ncx,tr3,dsl,xps,oxps,msp,msu,wsf,vbe,jse,psc1,psd1,rpm,pkg_tar_zst,aab,appx,msix,pfb,pfm,dfont,suit,bdf,pcf,tab_mapinfo,mid_mapinfo,mif,gdb_esri,dem,ecw,jks,keystore,ppk,asc,bkf,edb,olk14Message,olk14Category,sav_spss,ibdata,vix"
        self.file_extensions.insert(0, extensions_str)
        self.file_extensions.pack(pady=5, fill=tk.X, padx=10)
        
        # Опции
        options_frame = ttk.Frame(self.file_tab)
        options_frame.pack(pady=10, fill=tk.X, padx=10)
        
        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Рекурсивный обход", variable=self.recursive_var).pack(side=tk.LEFT, padx=5)
        
        self.delete_original = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="Удалить исходные файлы", variable=self.delete_original).pack(side=tk.LEFT, padx=5)
        
        # Кнопки
        btn_frame = ttk.Frame(self.file_tab)
        btn_frame.pack(pady=10)
        
        ttk.Button(btn_frame, text="Зашифровать папку", command=self.encrypt_folder).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Расшифровать папку", command=self.decrypt_folder).pack(side=tk.LEFT, padx=5)
        
        # Лог операций с прокруткой
        log_frame = ttk.Frame(self.file_tab)
        log_frame.pack(pady=5, fill=tk.BOTH, expand=True)
        
        ttk.Label(log_frame, text="Лог операций:").pack(anchor=tk.W)
        
        # Создаем фрейм для лога и скроллбара
        log_container = ttk.Frame(log_frame)
        log_container.pack(fill=tk.BOTH, expand=True)
        
        # Добавляем скроллбар
        scrollbar = ttk.Scrollbar(log_container)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.file_log = tk.Text(log_container, height=10, yscrollcommand=scrollbar.set)
        self.file_log.pack(pady=5, fill=tk.BOTH, expand=True, padx=10)
        self.file_log.config(state=tk.DISABLED)
        
        scrollbar.config(command=self.file_log.yview)
        
        # Прогресс-бар с отображением процентов
        progress_frame = ttk.Frame(self.file_tab)
        progress_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Метка для отображения процентов
        self.file_progress_var = tk.StringVar(value="Прогресс: 0%")
        ttk.Label(progress_frame, textvariable=self.file_progress_var).pack(anchor=tk.W)
        
        # Прогресс-бар
        self.progress = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.progress.pack(fill=tk.X, pady=2)
    
    def setup_about_tab(self):
        about_text = """
        Криптографическая программа
        
        Функции:
        1. Симметричное шифрование (AES-256)
        2. Асимметричное шифрование (RSA-2048)
        3. Шифрование файлов в папках
        4. Шифрование целых дисков (только Windows)
        
        Автор: Поляков Максим Павлович
        Электронная почта: comp-48@mail.ru
        
        Используемые технологии:
        - Python 3.8+
        - Библиотека cryptography
        - Стандартная библиотека tkinter
        
        Особенности:
        - Стойкое шифрование с использованием современных алгоритмов
        - Генерация криптографически безопасных ключей
        - Пакетное шифрование файлов
        - Рекурсивная обработка папок и дисков
        - Копирование/вставка ключей и текста
        - Загрузка ключей из файлов
        - Поддержка всех популярных форматов файлов
        - Интуитивно понятный интерфейс
        - Отдельный выбор источников для шифрования и расшифровки
        - Отображение прогресса в процентах
        - Прокрутка логов операций
        - Уведомления о завершении операций
        """
        
        ttk.Label(self.about_tab, text=about_text, justify=tk.LEFT).pack(pady=20, padx=10, anchor=tk.W)
    
    def select_folder(self):
        folder = filedialog.askdirectory(title="Выберите папку для шифрования")
        if folder:
            self.folder_path.set(folder)
    
    def select_path(self, path_type, operation_type):
        if path_type == "folder":
            path = filedialog.askdirectory(title=f"Выберите папку для {operation_type}")
        elif path_type == "drive" and platform.system() == "Windows":
            # Получаем список доступных дисков
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            if not drives:
                messagebox.showerror("Ошибка", "Доступные диски не найдены")
                return
            
            # Создаем диалог выбора диска
            drive_dialog = tk.Toplevel(self.root)
            drive_dialog.title(f"Выберите диск для {operation_type}")
            drive_dialog.geometry("300x200")
            drive_dialog.resizable(False, False)
            drive_dialog.transient(self.root)
            drive_dialog.grab_set()
            
            ttk.Label(drive_dialog, text=f"Выберите диск для {operation_type}:").pack(pady=10)
            
            drive_frame = ttk.Frame(drive_dialog)
            drive_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
            
            for drive in drives:
                ttk.Button(
                    drive_frame, 
                    text=drive, 
                    width=20,
                    command=lambda d=drive, op=operation_type: self.set_drive(d, op, drive_dialog)
                ).pack(pady=2)
            
            return
        else:
            return
        
        if path:
            if operation_type == "encrypt":
                self.encrypt_path.set(path)
            else:
                self.decrypt_path.set(path)
    
    def set_drive(self, drive, operation_type, dialog):
        if operation_type == "encrypt":
            self.encrypt_path.set(drive)
        else:
            self.decrypt_path.set(drive)
        dialog.destroy()
    
    def derive_key(self, password, salt):
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))
    
    def log_message(self, message, tab="file", asym=False):
        def _log():
            if tab == "file":
                if asym:
                    self.asym_log.config(state=tk.NORMAL)
                    self.asym_log.insert(tk.END, message + "\n")
                    self.asym_log.see(tk.END)
                    self.asym_log.config(state=tk.DISABLED)
                else:
                    self.file_log.config(state=tk.NORMAL)
                    self.file_log.insert(tk.END, message + "\n")
                    self.file_log.see(tk.END)
                    self.file_log.config(state=tk.DISABLED)
            else:
                self.sym_result.insert(tk.END, message + "\n")
        self.root.after(0, _log)
    
    def encrypt_file(self, file_path, password):
        try:
            salt = os.urandom(16)
            key = self.derive_key(password, salt)
            f = Fernet(key)
            
            with open(file_path, 'rb') as file:
                original = file.read()
            
            encrypted = f.encrypt(original)
            
            # Сохраняем соль вместе с зашифрованными данными
            with open(file_path + '.enc', 'wb') as file:
                file.write(salt + encrypted)
            
            return True
        except Exception as e:
            self.log_message(f"Ошибка при шифровании {file_path}: {str(e)}")
            return False
    
    def decrypt_file(self, file_path, password):
        try:
            with open(file_path, 'rb') as file:
                data = file.read()
            
            salt = data[:16]
            encrypted = data[16:]
            
            key = self.derive_key(password, salt)
            f = Fernet(key)
            decrypted = f.decrypt(encrypted)
            
            # Удаляем расширение .enc при сохранении
            if file_path.endswith('.enc'):
                output_path = file_path[:-4]
            else:
                output_path = file_path + '.dec'
                
            with open(output_path, 'wb') as file:
                file.write(decrypted)
            
            return True
        except Exception as e:
            self.log_message(f"Ошибка при расшифровке {file_path}: {str(e)}")
            return False
    
    def encrypt_file_asymmetric(self, file_path):
        """Шифрование файла с использованием гибридного подхода (RSA + AES)"""
        try:
            # Генерируем случайный симметричный ключ
            symmetric_key = Fernet.generate_key()
            f = Fernet(symmetric_key)
            
            # Шифруем файл с помощью симметричного ключа
            with open(file_path, 'rb') as file:
                original = file.read()
            
            encrypted_data = f.encrypt(original)
            
            # Шифруем симметричный ключ с помощью RSA
            encrypted_key = self.public_key.encrypt(
                symmetric_key,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )
            
            # Сохраняем зашифрованный ключ и данные
            with open(file_path + '.rsa', 'wb') as file:
                file.write(encrypted_key + encrypted_data)
            
            return True
        except Exception as e:
            self.log_message(f"Ошибка при асимметричном шифровании {file_path}: {str(e)}", asym=True)
            return False
    
    def decrypt_file_asymmetric(self, file_path):
        """Дешифрование файла с использованием гибридного подхода (RSA + AES)"""
        try:
            with open(file_path, 'rb') as file:
                data = file.read()
            
            # Фиксированный размер зашифрованного ключа для RSA-2048
            key_size = 256  # 2048 бит / 8 байт
            encrypted_key = data[:key_size]
            encrypted_data = data[key_size:]
            
            # Дешифруем симметричный ключ
            symmetric_key = self.private_key.decrypt(
                encrypted_key,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )
            
            # Дешифруем данные с помощью симметричного ключа
            f = Fernet(symmetric_key)
            decrypted_data = f.decrypt(encrypted_data)
            
            # Сохраняем дешифрованные данные
            if file_path.endswith('.rsa'):
                output_path = file_path[:-4]
            else:
                output_path = file_path + '.dec'
                
            with open(output_path, 'wb') as file:
                file.write(decrypted_data)
            
            return True
        except Exception as e:
            self.log_message(f"Ошибка при асимметричной расшифровке {file_path}: {str(e)}", asym=True)
            return False
    
    def process_folder(self, operation, password, extensions, recursive, delete_original, asymmetric=False, path=None):
        if not path:
            if asymmetric and operation == "encrypt":
                path = self.encrypt_path.get()
            elif asymmetric and operation == "decrypt":
                path = self.decrypt_path.get()
            else:
                path = self.folder_path.get()
        
        if not path or not os.path.exists(path):
            messagebox.showerror("Ошибка", "Выберите корректный путь")
            return
        
        # Получаем список файлов с правильной фильтрацией по расширениям
        files_to_process = []
        if recursive:
            for root, dirs, files in os.walk(path):
                # Пропускаем папки с ошибками доступа
                try:
                    for file in files:
                        file_path = os.path.join(root, file)
                        _, file_ext = os.path.splitext(file)
                        if file_ext.lower() in extensions:
                            files_to_process.append(file_path)
                except PermissionError:
                    continue
        else:
            if os.path.isfile(path):
                _, file_ext = os.path.splitext(path)
                if file_ext.lower() in extensions:
                    files_to_process = [path]
            else:
                for file in os.listdir(path):
                    file_path = os.path.join(path, file)
                    if os.path.isfile(file_path):
                        _, file_ext = os.path.splitext(file)
                        if file_ext.lower() in extensions:
                            files_to_process.append(file_path)
        
        total_files = len(files_to_process)
        if total_files == 0:
            self.log_message("Файлы для обработки не найдены", asym=asymmetric)
            return
        
        if asymmetric:
            self.root.after(0, lambda: self.asym_progress.config(maximum=total_files, value=0))
            self.root.after(0, lambda: self.asym_progress_var.set("Прогресс: 0%"))
            self.root.after(0, lambda: self.current_file_var.set("Подготовка к обработке..."))
        else:
            self.root.after(0, lambda: self.progress.config(maximum=total_files, value=0))
            self.root.after(0, lambda: self.file_progress_var.set("Прогресс: 0%"))
        
        # Обрабатываем файлы
        success_count = 0
        for i, file_path in enumerate(files_to_process):
            try:
                # Обновляем информацию о текущем файле
                short_path = os.path.basename(file_path)
                if asymmetric:
                    self.root.after(0, lambda: self.current_file_var.set(f"Обработка: {short_path}..."))
                
                if operation == "encrypt":
                    if asymmetric:
                        result = self.encrypt_file_asymmetric(file_path)
                    else:
                        result = self.encrypt_file(file_path, password)
                    
                    # Удаляем оригинал только после успешного шифрования
                    if result and delete_original:
                        try:
                            os.remove(file_path)
                            self.log_message(f"Удален исходный файл: {file_path}", asym=asymmetric)
                        except Exception as e:
                            self.log_message(f"Ошибка удаления файла: {file_path} - {str(e)}", asym=asymmetric)
                else:
                    if asymmetric and file_path.endswith('.rsa'):
                        result = self.decrypt_file_asymmetric(file_path)
                    elif not asymmetric and file_path.endswith('.enc'):
                        result = self.decrypt_file(file_path, password)
                    else:
                        ext = ".rsa" if asymmetric else ".enc"
                        self.log_message(f"Пропущен файл (не {ext}): {file_path}", asym=asymmetric)
                        result = False
                    
                    # Удаляем зашифрованный файл только после успешной расшифровки
                    if result and delete_original:
                        try:
                            os.remove(file_path)
                            self.log_message(f"Удален зашифрованный файл: {file_path}", asym=asymmetric)
                        except Exception as e:
                            self.log_message(f"Ошибка удаления файла: {file_path} - {str(e)}", asym=asymmetric)
                
                if result:
                    success_count += 1
                    self.log_message(f"Успешно: {file_path}", asym=asymmetric)
                else:
                    self.log_message(f"Ошибка: {file_path}", asym=asymmetric)
            except Exception as e:
                self.log_message(f"Критическая ошибка: {file_path} - {str(e)}", asym=asymmetric)
            
            # Обновляем прогресс
            progress_value = i + 1
            percentage = int(progress_value / total_files * 100)
            
            if asymmetric:
                self.root.after(0, lambda v=progress_value: self.asym_progress.config(value=v))
                self.root.after(0, lambda p=percentage: self.asym_progress_var.set(f"Прогресс: {p}%"))
            else:
                self.root.after(0, lambda v=progress_value: self.progress.config(value=v))
                self.root.after(0, lambda p=percentage: self.file_progress_var.set(f"Прогресс: {p}%"))
        
        if asymmetric:
            self.root.after(0, lambda: self.current_file_var.set("Обработка завершена"))
        self.log_message(f"\nОбработка завершена! Успешно: {success_count}/{total_files}", asym=asymmetric)
        
        # Возвращаем статистику для уведомления
        return success_count, total_files
    
    def encrypt_folder(self):
        password = self.file_password.get().strip()
        if not password:
            messagebox.showerror("Ошибка", "Введите пароль")
            return
        
        extensions_input = self.file_extensions.get().strip()
        if not extensions_input:
            messagebox.showerror("Ошибка", "Укажите расширения файлов")
            return
        
        # Формируем множество расширений с точками
        extensions = set()
        for ext in extensions_input.split(","):
            ext_clean = ext.strip().lower()
            if ext_clean:
                if not ext_clean.startswith("."):
                    ext_clean = "." + ext_clean
                extensions.add(ext_clean)
        
        # Запускаем в отдельном потоке
        def run_encryption():
            try:
                success, total = self.process_folder(
                    "encrypt",
                    password,
                    extensions,
                    self.recursive_var.get(),
                    self.delete_original.get(),
                    False
                )
                self.root.after(0, lambda: messagebox.showinfo(
                    "Шифрование завершено",
                    f"Успешно зашифровано: {success}/{total} файлов\n\nПроверьте лог операций для деталей."
                ))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "Ошибка",
                    f"Произошла ошибка при шифровании: {str(e)}"
                ))
        
        threading.Thread(target=run_encryption, daemon=True).start()
    
    def decrypt_folder(self):
        password = self.file_password.get().strip()
        if not password:
            messagebox.showerror("Ошибка", "Введите пароль")
            return
        
        # Для расшифровки используем только .enc файлы
        extensions = {".enc"}
        
        # Запускаем в отдельном потоке
        def run_decryption():
            try:
                success, total = self.process_folder(
                    "decrypt",
                    password,
                    extensions,
                    self.recursive_var.get(),
                    self.delete_original.get(),
                    False
                )
                self.root.after(0, lambda: messagebox.showinfo(
                    "Расшифровка завершена",
                    f"Успешно расшифровано: {success}/{total} файлов\n\nПроверьте лог операций для деталей."
                ))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "Ошибка",
                    f"Произошла ошибка при расшифровке: {str(e)}"
                ))
        
        threading.Thread(target=run_decryption, daemon=True).start()
    
    def encrypt_asym_folder(self):
        # Проверяем загружен ли публичный ключ
        if not self.public_key:
            messagebox.showerror("Ошибка", "Сначала загрузите публичный ключ")
            return
        
        path = self.encrypt_path.get()
        if not path or not os.path.exists(path):
            messagebox.showerror("Ошибка", "Выберите корректный путь для шифрования")
            return
        
        extensions_input = self.asym_extensions.get().strip()
        if not extensions_input:
            messagebox.showerror("Ошибка", "Укажите расширения файлов")
            return
        
        # Формируем множество расширений с точками
        extensions = set()
        for ext in extensions_input.split(","):
            ext_clean = ext.strip().lower()
            if ext_clean:
                if not ext_clean.startswith("."):
                    ext_clean = "." + ext_clean
                extensions.add(ext_clean)
        
        # Запускаем в отдельном потоке
        def run_asym_encryption():
            try:
                success, total = self.process_folder(
                    "encrypt",
                    None,
                    extensions,
                    self.asym_recursive.get(),
                    self.asym_delete_original.get(),
                    True,
                    path
                )
                self.root.after(0, lambda: messagebox.showinfo(
                    "Асимметричное шифрование завершено",
                    f"Успешно зашифровано: {success}/{total} файлов\n\nПроверьте лог операций для деталей."
                ))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "Ошибка",
                    f"Произошла ошибка при асимметричном шифровании: {str(e)}"
                ))
        
        threading.Thread(target=run_asym_encryption, daemon=True).start()
    
    def decrypt_asym_folder(self):
        # Проверяем загружен ли приватный ключ
        if not self.private_key:
            messagebox.showerror("Ошибка", "Сначала загрузите приватный ключ")
            return
        
        path = self.decrypt_path.get()
        if not path or not os.path.exists(path):
            messagebox.showerror("Ошибка", "Выберите корректный путь для расшифровки")
            return
        
        # Для расшифровки используем только .rsa файлы
        extensions = {".rsa"}
        
        # Запускаем в отдельном потоке
        def run_asym_decryption():
            try:
                success, total = self.process_folder(
                    "decrypt",
                    None,
                    extensions,
                    self.asym_recursive.get(),
                    self.asym_delete_original.get(),
                    True,
                    path
                )
                self.root.after(0, lambda: messagebox.showinfo(
                    "Асимметричная расшифровка завершена",
                    f"Успешно расшифровано: {success}/{total} файлов\n\nПроверьте лог операций для деталей."
                ))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "Ошибка",
                    f"Произошла ошибка при асимметричной расшифровке: {str(e)}"
                ))
        
        threading.Thread(target=run_asym_decryption, daemon=True).start()
    
    def encrypt_symmetric(self):
        text = self.sym_text.get("1.0", tk.END).strip()
        password = self.password_entry.get().strip()
        
        if not text or not password:
            messagebox.showerror("Ошибка", "Введите текст и пароль")
            return
        
        try:
            salt = os.urandom(16)
            key = self.derive_key(password, salt)
            f = Fernet(key)
            encrypted = f.encrypt(text.encode())
            
            # Сохраняем соль вместе с зашифрованными данными
            result = base64.urlsafe_b64encode(salt + encrypted).decode()
            self.sym_result.delete("1.0", tk.END)
            self.sym_result.insert(tk.END, result)
            messagebox.showinfo("Успех", "Текст успешно зашифрован!")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка шифрования: {str(e)}")
    
    def decrypt_symmetric(self):
        text = self.sym_text.get("1.0", tk.END).strip()
        password = self.password_entry.get().strip()
        
        if not text or not password:
            messagebox.showerror("Ошибка", "Введите текст и пароль")
            return
        
        try:
            data = base64.urlsafe_b64decode(text)
            salt = data[:16]
            encrypted = data[16:]
            
            key = self.derive_key(password, salt)
            f = Fernet(key)
            decrypted = f.decrypt(encrypted).decode()
            
            self.sym_result.delete("1.0", tk.END)
            self.sym_result.insert(tk.END, decrypted)
            messagebox.showinfo("Успех", "Текст успешно расшифрован!")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка расшифровки: {str(e)}")
    
    def generate_keys(self):
        try:
            # Генерация приватного ключа
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend()
            )
            
            # Получение публичного ключа
            public_key = private_key.public_key()
            
            # Сохранение ключей в файлы
            private_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            
            public_pem = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
            
            # Запрос места сохранения
            private_path = filedialog.asksaveasfilename(
                title="Сохранить приватный ключ",
                filetypes=(("PEM files", "*.pem"), ("All files", "*.*")),
                defaultextension=".pem"
            )
            
            if private_path:
                with open(private_path, 'wb') as f:
                    f.write(private_pem)
            
            public_path = filedialog.asksaveasfilename(
                title="Сохранить публичный ключ",
                filetypes=(("PEM files", "*.pem"), ("All files", "*.*")),
                defaultextension=".pem"
            )
            
            if public_path:
                with open(public_path, 'wb') as f:
                    f.write(public_pem)
            
            messagebox.showinfo("Успех", "Ключи успешно сгенерированы и сохранены")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка генерации ключей: {str(e)}")
    
    def load_public_key(self):
        """Загрузка публичного ключа из файла"""
        file_path = filedialog.askopenfilename(
            title="Выберите файл публичного ключа",
            filetypes=(("PEM files", "*.pem"), ("All files", "*.*"))
        )
        if file_path:
            try:
                with open(file_path, "rb") as key_file:
                    public_key = serialization.load_pem_public_key(
                        key_file.read(),
                        backend=default_backend()
                    )
                self.public_key = public_key
                messagebox.showinfo("Успех", "Публичный ключ успешно загружен")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Ошибка загрузки ключа: {str(e)}")
    
    def load_private_key(self):
        """Загрузка приватного ключа из файла"""
        file_path = filedialog.askopenfilename(
            title="Выберите файл приватного ключа",
            filetypes=(("PEM files", "*.pem"), ("All files", "*.*"))
        )
        if file_path:
            try:
                with open(file_path, "rb") as key_file:
                    private_key = serialization.load_pem_private_key(
                        key_file.read(),
                        password=None,
                        backend=default_backend()
                    )
                self.private_key = private_key
                messagebox.showinfo("Успех", "Приватный ключ успешно загружен")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Ошибка загрузки ключа: {str(e)}")
    
    def encrypt_asymmetric(self):
        text = self.asym_text.get("1.0", tk.END).strip()
        
        if not text:
            messagebox.showerror("Ошибка", "Введите текст")
            return
        
        # Если ключ не загружен, попросим загрузить
        if not self.public_key:
            messagebox.showinfo("Информация", "Сначала загрузите публичный ключ")
            return
        
        try:
            # Шифрование с использованием загруженного ключа
            encrypted = self.public_key.encrypt(
                text.encode(),
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )
            
            result = base64.b64encode(encrypted).decode()
            self.asym_result.delete("1.0", tk.END)
            self.asym_result.insert(tk.END, result)
            messagebox.showinfo("Успех", "Текст успешно зашифрован!")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка шифрования: {str(e)}")
    
    def decrypt_asymmetric(self):
        text = self.asym_text.get("1.0", tk.END).strip()
        
        if not text:
            messagebox.showerror("Ошибка", "Введите текст")
            return
        
        # Если ключ не загружен, попросим загрузить
        if not self.private_key:
            messagebox.showinfo("Информация", "Сначала загрузите приватный ключ")
            return
        
        try:
            # Расшифровка с использованием загруженного ключа
            decrypted = self.private_key.decrypt(
                base64.b64decode(text),
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            ).decode()
            
            self.asym_result.delete("1.0", tk.END)
            self.asym_result.insert(tk.END, decrypted)
            messagebox.showinfo("Успех", "Текст успешно расшифрован!")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка расшифровки: {str(e)}")
    
    def copy_password(self):
        """Копирование пароля в буфер обмена"""
        password = self.password_entry.get()
        if password:
            self.root.clipboard_clear()
            self.root.clipboard_append(password)
            messagebox.showinfo("Успех", "Пароль скопирован в буфер обмена")
        else:
            messagebox.showwarning("Пусто", "Поле пароля пустое")
    
    def paste_password(self):
        """Вставка пароля из буфера обмена"""
        try:
            password = self.root.clipboard_get()
            if password:
                self.password_entry.delete(0, tk.END)
                self.password_entry.insert(0, password)
            else:
                messagebox.showwarning("Пусто", "Буфер обмена пуст")
        except tk.TclError:
            messagebox.showwarning("Ошибка", "Невозможно получить данные из буфера обмена")
    
    def copy_symmetric_result(self):
        """Копирование результата симметричного шифрования"""
        result = self.sym_result.get("1.0", tk.END).strip()
        if result:
            self.root.clipboard_clear()
            self.root.clipboard_append(result)
            messagebox.showinfo("Успех", "Результат скопирован в буфер обмена")
        else:
            messagebox.showwarning("Пусто", "Результат пуст")
    
    def paste_text(self):
        """Вставка текста из буфера обмена"""
        try:
            text = self.root.clipboard_get()
            if text:
                self.sym_text.delete("1.0", tk.END)
                self.sym_text.insert(tk.END, text)
            else:
                messagebox.showwarning("Пусто", "Буфер обмена пуст")
        except tk.TclError:
            messagebox.showwarning("Ошибка", "Невозможно получить данные из буфера обмена")
    
    def copy_asymmetric_result(self):
        """Копирование результата асимметричного шифрования"""
        result = self.asym_result.get("1.0", tk.END).strip()
        if result:
            self.root.clipboard_clear()
            self.root.clipboard_append(result)
            messagebox.showinfo("Успех", "Результат скопирован в буфер обмена")
        else:
            messagebox.showwarning("Пусто", "Результат пуст")
    
    def paste_asym_text(self):
        """Вставка текста в поле асимметричного шифрования"""
        try:
            text = self.root.clipboard_get()
            if text:
                self.asym_text.delete("1.0", tk.END)
                self.asym_text.insert(tk.END, text)
            else:
                messagebox.showwarning("Пусто", "Буфер обмена пуст")
        except tk.TclError:
            messagebox.showwarning("Ошибка", "Невозможно получить данные из буфера обмена")
    
    def clear_symmetric(self):
        self.sym_text.delete("1.0", tk.END)
        self.sym_result.delete("1.0", tk.END)
        self.password_entry.delete(0, tk.END)
    
    def clear_asymmetric(self):
        self.asym_text.delete("1.0", tk.END)
        self.asym_result.delete("1.0", tk.END)

if __name__ == "__main__":
    root = tk.Tk()
    app = CryptoApp(root)
    root.mainloop()
