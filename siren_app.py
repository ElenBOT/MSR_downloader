# -*- coding: utf-8 -*-
"""
賽任唱片下載器

GUI 工具，分成兩個分頁：

標籤編輯
- 瀏覽資料夾中的 MP3 / FLAC
- 編輯常見 metadata、封面，儲存回檔案

搜尋下載
- 搜尋塞壬唱片官方曲庫並下載音源
- wav 轉 FLAC、歌詞嵌入可開關（預設開啟）
- 歌詞：官方 lyricUrl + LRCLIB，自動選較佳並嵌入音檔

需求：pip install mutagen pillow
下載時若要把官方 wav 轉成 FLAC，需要 ffmpeg（加入 PATH）。
"""
import os
import io
import sys
import glob
import json
import shutil
import tempfile
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TPE2, TCOM, TCON, TDRC, TRCK, APIC, USLT, error as ID3Error

import msr_api
from lyrics_sources import looks_instrumental, pick_best_lyrics

SUPPORTED_EXT = (".mp3", ".flac")
COVER_BOX_SIZE = 260
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")


def default_music_dir():
    if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
        return os.path.abspath(sys.argv[1])
    return os.path.dirname(APP_DIR)


def load_settings():
    data = {
        "music_dir": default_music_dir(),
        "convert_wav_to_flac": True,
        "embed_lyrics": True,
        "save_lyrics_file": False,
    }
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            data.update(saved)
    except (OSError, json.JSONDecodeError):
        pass
    music_dir = data.get("music_dir") or default_music_dir()
    if not os.path.isdir(music_dir):
        music_dir = default_music_dir()
    data["music_dir"] = os.path.abspath(music_dir)
    return data


def save_settings(data):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def wav_bytes_to_flac_bytes(wav_bytes):
    """用 ffmpeg 把記憶體中的 wav bytes 轉成 flac bytes（下載流程用）。"""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("找不到 ffmpeg，請先安裝並加入 PATH。")
    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "src.wav")
        flac_path = os.path.join(tmpdir, "out.flac")
        with open(wav_path, "wb") as f:
            f.write(wav_bytes)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", wav_path,
            "-map", "0",
            "-map_metadata", "0",
            "-c:a", "flac",
            "-compression_level", "8",
            flac_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not os.path.exists(flac_path):
            raise RuntimeError("ffmpeg 轉檔失敗：\n" + (result.stderr or "").strip()[-800:])
        with open(flac_path, "rb") as f:
            return f.read()


def save_flac_tags(path, values, cover_bytes, cover_mime, lyrics_text=None):
    audio = FLAC(path)
    mapping = {
        "title": "TITLE",
        "artist": "ARTIST",
        "album": "ALBUM",
        "albumartist": "ALBUMARTIST",
        "composer": "COMPOSER",
        "genre": "GENRE",
        "date": "DATE",
        "tracknumber": "TRACKNUMBER",
    }
    for key, tag in mapping.items():
        value = values.get(key, "")
        if value:
            audio[tag] = value
        elif tag in audio:
            del audio[tag]

    # 同步歌詞以 LRC 原文寫入 LYRICS（多數支援內嵌歌詞的播放器會讀這個欄位）
    # lyrics_text=None 表示不更動既有歌詞；空字串則清除。
    if lyrics_text is not None:
        if lyrics_text:
            audio["LYRICS"] = lyrics_text
        elif "LYRICS" in audio:
            del audio["LYRICS"]

    audio.clear_pictures()
    if cover_bytes:
        pic = Picture()
        pic.data = cover_bytes
        pic.type = 3
        pic.mime = cover_mime or "image/jpeg"
        audio.add_picture(pic)
    audio.save()


def save_mp3_tags(path, values, cover_bytes, cover_mime, lyrics_text=None):
    try:
        audio = ID3(path)
    except ID3Error:
        audio = ID3()

    frame_classes = {
        "title": TIT2, "artist": TPE1, "album": TALB, "albumartist": TPE2,
        "composer": TCOM, "genre": TCON, "date": TDRC, "tracknumber": TRCK,
    }
    for key, FrameCls in frame_classes.items():
        fid = FrameCls.__name__
        value = values.get(key, "")
        if value:
            audio[fid] = FrameCls(encoding=3, text=value)
        elif fid in audio:
            audio.delall(fid)

    audio.delall("APIC")
    if cover_bytes:
        audio["APIC"] = APIC(encoding=3, mime=cover_mime or "image/jpeg", type=3, desc="Cover", data=cover_bytes)

    # lyrics_text=None 表示不更動既有歌詞；有內容則覆寫；空字串則清除。
    if lyrics_text is not None:
        audio.delall("USLT")
        if lyrics_text:
            # 直接把 LRC 原文放進 USLT；支援解析時間戳的播放器仍可同步顯示
            audio.add(USLT(encoding=3, lang="chi", desc="", text=lyrics_text))

    audio.save(path, v2_version=3)


class SirenApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("賽任唱片下載器")
        self.geometry("1040x620")
        self.minsize(900, 540)

        self.settings = load_settings()
        self.current_dir = self.settings["music_dir"]
        self.current_file = None
        self.cover_bytes = None      # 目前封面的原始 bytes（準備寫入檔案用）
        self.cover_mime = "image/jpeg"
        self.cover_photo = None      # Tk PhotoImage（畫面顯示用，需保留參照）
        self.msr_song_list = None    # 塞壬唱片歌曲清單快取（第一次抓取後保留）
        self.msr_album_name_cache = {}  # albumCid -> 專輯名（含 coverUrl 等）
        self.msr_matches = []        # 目前搜尋結果
        self.msr_selected_song = None
        self.msr_selected_detail = None  # 含 sourceUrl
        self.msr_selected_album = None
        self.dl_cover_bytes = None
        self.dl_cover_mime = "image/jpeg"
        self.dl_cover_photo = None

        self._build_ui()
        self._reload_file_list()

    def _persist_settings(self):
        self.settings["music_dir"] = self.current_dir
        self.settings["convert_wav_to_flac"] = bool(self.convert_wav_to_flac_var.get())
        self.settings["embed_lyrics"] = bool(self.embed_lyrics_var.get())
        self.settings["save_lyrics_file"] = bool(self.save_lyrics_file_var.get())
        save_settings(self.settings)

    # ---------- UI ----------
    def _build_ui(self):
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        # 共用：音樂資料夾（標籤編輯瀏覽／下載預設存檔位置）
        path_bar = ttk.Frame(root)
        path_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        path_bar.columnconfigure(1, weight=1)
        ttk.Label(path_bar, text="音樂資料夾").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.music_dir_var = tk.StringVar(value=self.current_dir)
        path_entry = ttk.Entry(path_bar, textvariable=self.music_dir_var)
        path_entry.grid(row=0, column=1, sticky="ew")
        path_entry.bind("<Return>", lambda _e: self._apply_music_dir_from_entry())
        path_entry.bind("<FocusOut>", lambda _e: self._apply_music_dir_from_entry())
        ttk.Button(path_bar, text="瀏覽...", command=self._choose_dir).grid(row=0, column=2, padx=(6, 0))

        notebook = ttk.Notebook(root)
        notebook.grid(row=1, column=0, sticky="nsew")

        edit_tab = ttk.Frame(notebook, padding=4)
        download_tab = ttk.Frame(notebook, padding=4)
        notebook.add(edit_tab, text="標籤編輯")
        notebook.add(download_tab, text="搜尋下載")

        self._build_edit_tab(edit_tab)
        self._build_download_tab(download_tab)

    def _build_edit_tab(self, parent):
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)

        # 左側：檔案清單
        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 8))

        top_bar = ttk.Frame(left)
        top_bar.pack(fill="x", pady=(0, 4))
        ttk.Button(top_bar, text="重新整理", command=self._reload_file_list).pack(side="left")
        ttk.Label(left, text="清單來自上方「音樂資料夾」", foreground="#777").pack(fill="x", pady=(0, 4))

        list_frame = ttk.Frame(left)
        list_frame.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.file_listbox = tk.Listbox(list_frame, width=36, yscrollcommand=scrollbar.set, exportselection=False)
        scrollbar.config(command=self.file_listbox.yview)
        self.file_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.file_listbox.bind("<<ListboxSelect>>", self._on_select_file)

        # 右側：欄位編輯區
        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)

        fields = [
            ("title", "標題 (Title)"),
            ("artist", "演唱者 / 演出者 (Artist)"),
            ("album", "專輯 (Album)"),
            ("albumartist", "專輯演出者 (Album Artist)"),
            ("composer", "作曲 (Composer)"),
            ("genre", "類型 (Genre)"),
            ("date", "年份 (Date)"),
            ("tracknumber", "音軌編號 (Track #)"),
        ]
        self.vars = {}
        for i, (key, label) in enumerate(fields):
            ttk.Label(right, text=label).grid(row=i, column=0, sticky="w", pady=3)
            var = tk.StringVar()
            entry = ttk.Entry(right, textvariable=var)
            entry.grid(row=i, column=1, sticky="ew", pady=3, padx=(6, 0))
            self.vars[key] = var

        # 封面圖片區
        cover_row = len(fields)
        cover_frame = ttk.LabelFrame(right, text="封面圖片")
        cover_frame.grid(row=cover_row, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        right.rowconfigure(cover_row, weight=1)

        self.cover_canvas = tk.Label(cover_frame, background="#ddd", width=COVER_BOX_SIZE, anchor="center")
        self.cover_canvas.pack(side="left", padx=8, pady=8)

        cover_btns = ttk.Frame(cover_frame)
        cover_btns.pack(side="left", fill="y", padx=8, pady=8)
        ttk.Button(cover_btns, text="選擇圖片檔案...", command=self._choose_cover_file).pack(fill="x", pady=2)
        ttk.Button(cover_btns, text="從剪貼簿貼上", command=self._paste_cover_from_clipboard).pack(fill="x", pady=2)
        ttk.Button(cover_btns, text="移除封面", command=self._remove_cover).pack(fill="x", pady=2)

        # 底部按鈕
        bottom = ttk.Frame(right)
        bottom.grid(row=cover_row + 1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(bottom, text="儲存變更", command=self._save_current).pack(side="right")
        self.status_label = ttk.Label(bottom, text="", foreground="#0a0")
        self.status_label.pack(side="left")

    def _build_download_tab(self, parent):
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)

        # 左側：搜尋 + 結果清單
        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 8))

        search_row = ttk.Frame(left)
        search_row.pack(fill="x", pady=(0, 4))
        search_row.columnconfigure(0, weight=1)
        self.msr_query_var = tk.StringVar()
        msr_entry = ttk.Entry(search_row, textvariable=self.msr_query_var)
        msr_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        msr_entry.bind("<Return>", lambda _e: self._msr_run_search())
        ttk.Button(search_row, text="搜尋", command=self._msr_run_search).grid(row=0, column=1)

        self.msr_result_hint = ttk.Label(left, text="搜尋塞壬唱片官方曲庫", wraplength=260, foreground="#555")
        self.msr_result_hint.pack(fill="x", pady=(0, 4))

        list_frame = ttk.Frame(left)
        list_frame.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.msr_result_listbox = tk.Listbox(
            list_frame, width=36, yscrollcommand=scrollbar.set, exportselection=False
        )
        scrollbar.config(command=self.msr_result_listbox.yview)
        self.msr_result_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.msr_result_listbox.bind("<<ListboxSelect>>", self._on_select_msr_result)

        # 右側：metadata（布局對齊標籤編輯）
        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)

        fields = [
            ("title", "標題 (Title)"),
            ("artist", "演唱者 / 演出者 (Artist)"),
            ("album", "專輯 (Album)"),
            ("albumartist", "專輯演出者 (Album Artist)"),
            ("composer", "作曲 (Composer)"),
            ("genre", "類型 (Genre)"),
            ("date", "年份 (Date)"),
            ("tracknumber", "音軌編號 (Track #)"),
        ]
        self.dl_vars = {}
        for i, (key, label) in enumerate(fields):
            ttk.Label(right, text=label).grid(row=i, column=0, sticky="w", pady=3)
            var = tk.StringVar()
            entry = ttk.Entry(right, textvariable=var)
            entry.grid(row=i, column=1, sticky="ew", pady=3, padx=(6, 0))
            self.dl_vars[key] = var

        cover_row = len(fields)
        cover_frame = ttk.LabelFrame(right, text="封面圖片")
        cover_frame.grid(row=cover_row, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        right.rowconfigure(cover_row, weight=1)

        self.dl_cover_canvas = tk.Label(cover_frame, background="#ddd", width=COVER_BOX_SIZE, anchor="center")
        self.dl_cover_canvas.pack(side="left", padx=8, pady=8)
        self.dl_cover_canvas.config(text="（無封面）")

        cover_note = ttk.Frame(cover_frame)
        cover_note.pack(side="left", fill="y", padx=8, pady=8)
        ttk.Label(
            cover_note,
            text="選取左側歌曲後會自動帶入官方封面與標籤。\nArtist 來自官方 artists；Composer 官方沒有，維持空白可手動補。",
            foreground="#777",
            wraplength=220,
            justify="left",
        ).pack(anchor="nw")

        bottom = ttk.Frame(right)
        bottom.grid(row=cover_row + 1, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        self.download_status_label = ttk.Label(bottom, text="", foreground="#0a0")
        self.download_status_label.pack(side="left")

        right_btns = ttk.Frame(bottom)
        right_btns.pack(side="right")
        self.convert_wav_to_flac_var = tk.BooleanVar(value=bool(self.settings.get("convert_wav_to_flac", True)))
        self.embed_lyrics_var = tk.BooleanVar(value=bool(self.settings.get("embed_lyrics", True)))
        self.save_lyrics_file_var = tk.BooleanVar(value=bool(self.settings.get("save_lyrics_file", False)))
        for text, var in (
            ("wav 轉成 FLAC", self.convert_wav_to_flac_var),
            ("嵌入歌詞", self.embed_lyrics_var),
            ("獨立歌詞檔", self.save_lyrics_file_var),
        ):
            cb = ttk.Checkbutton(right_btns, text=text, variable=var, command=self._persist_settings)
            cb.pack(side="left", padx=(0, 8))
        ttk.Button(right_btns, text="下載音檔", command=self._msr_download_new_song).pack(side="left")

    # ---------- 檔案清單 / 路徑 ----------
    def _apply_music_dir_from_entry(self):
        path = self.music_dir_var.get().strip().strip('"')
        if not path:
            self.music_dir_var.set(self.current_dir)
            return
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            messagebox.showwarning("資料夾不存在", f"找不到資料夾：\n{path}")
            self.music_dir_var.set(self.current_dir)
            return
        if os.path.normcase(path) == os.path.normcase(self.current_dir):
            self.music_dir_var.set(self.current_dir)
            return
        self.current_dir = path
        self.music_dir_var.set(self.current_dir)
        self._persist_settings()
        self._reload_file_list()

    def _choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.current_dir)
        if d:
            self.current_dir = os.path.abspath(d)
            self.music_dir_var.set(self.current_dir)
            self._persist_settings()
            self._reload_file_list()

    def _reload_file_list(self):
        self.file_listbox.delete(0, "end")
        self.files = []
        for ext in SUPPORTED_EXT:
            self.files.extend(glob.glob(os.path.join(self.current_dir, f"*{ext}")))
        self.files.sort(key=lambda p: os.path.basename(p).lower())
        for p in self.files:
            self.file_listbox.insert("end", os.path.basename(p))
        self._clear_fields()

    def _on_select_file(self, _event=None):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        path = self.files[sel[0]]
        self._load_file(path)

    # ---------- 讀取 / 顯示 ----------
    def _clear_fields(self):
        self.current_file = None
        for var in self.vars.values():
            var.set("")
        self.cover_bytes = None
        self.cover_photo = None
        self.cover_canvas.config(image="", text="（無封面）")
        self.status_label.config(text="")

    def _load_file(self, path):
        self.current_file = path
        for var in self.vars.values():
            var.set("")
        self.cover_bytes = None
        self.status_label.config(text="")

        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".flac":
                audio = FLAC(path)
                self.vars["title"].set(self._get1(audio, "TITLE"))
                self.vars["artist"].set(self._get1(audio, "ARTIST"))
                self.vars["album"].set(self._get1(audio, "ALBUM"))
                self.vars["albumartist"].set(self._get1(audio, "ALBUMARTIST"))
                self.vars["composer"].set(self._get1(audio, "COMPOSER"))
                self.vars["genre"].set(self._get1(audio, "GENRE"))
                self.vars["date"].set(self._get1(audio, "DATE"))
                self.vars["tracknumber"].set(self._get1(audio, "TRACKNUMBER"))
                if audio.pictures:
                    self.cover_bytes = audio.pictures[0].data
                    self.cover_mime = audio.pictures[0].mime or "image/jpeg"
            else:
                try:
                    audio = ID3(path)
                except ID3Error:
                    audio = ID3()
                self.vars["title"].set(self._id3_text(audio, "TIT2"))
                self.vars["artist"].set(self._id3_text(audio, "TPE1"))
                self.vars["album"].set(self._id3_text(audio, "TALB"))
                self.vars["albumartist"].set(self._id3_text(audio, "TPE2"))
                self.vars["composer"].set(self._id3_text(audio, "TCOM"))
                self.vars["genre"].set(self._id3_text(audio, "TCON"))
                self.vars["date"].set(self._id3_text(audio, "TDRC"))
                self.vars["tracknumber"].set(self._id3_text(audio, "TRCK"))
                apics = audio.getall("APIC")
                if apics:
                    self.cover_bytes = apics[0].data
                    self.cover_mime = apics[0].mime or "image/jpeg"
        except Exception as e:
            messagebox.showerror("讀取失敗", f"無法讀取檔案標籤：\n{e}")
            return

        self._refresh_cover_preview()

    @staticmethod
    def _get1(flac_audio, key):
        vals = flac_audio.get(key)
        return vals[0] if vals else ""

    @staticmethod
    def _id3_text(id3_audio, frame_id):
        frame = id3_audio.get(frame_id)
        return str(frame.text[0]) if frame and frame.text else ""

    def _refresh_cover_preview(self):
        if not self.cover_bytes:
            self.cover_photo = None
            self.cover_canvas.config(image="", text="（無封面）")
            return
        try:
            img = Image.open(io.BytesIO(self.cover_bytes))
            img.thumbnail((COVER_BOX_SIZE, COVER_BOX_SIZE))
            self.cover_photo = ImageTk.PhotoImage(img)
            self.cover_canvas.config(image=self.cover_photo, text="")
        except Exception:
            self.cover_photo = None
            self.cover_canvas.config(image="", text="（無法預覽此圖片）")

    # ---------- 封面操作 ----------
    def _choose_cover_file(self):
        path = filedialog.askopenfilename(
            title="選擇封面圖片",
            filetypes=[("圖片檔案", "*.jpg *.jpeg *.png *.webp *.bmp"), ("所有檔案", "*.*")],
        )
        if not path:
            return
        self._set_cover_from_path(path)

    def _set_cover_from_path(self, path):
        try:
            img = Image.open(path)
            img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=92)
            self.cover_bytes = buf.getvalue()
            self.cover_mime = "image/jpeg"
        except Exception as e:
            messagebox.showerror("圖片載入失敗", f"{e}")
            return
        self._refresh_cover_preview()

    def _paste_cover_from_clipboard(self):
        try:
            from PIL import ImageGrab
            img = ImageGrab.grabclipboard()
        except Exception as e:
            messagebox.showerror("剪貼簿讀取失敗", f"{e}")
            return
        if img is None:
            messagebox.showwarning("剪貼簿沒有圖片", "請先複製一張圖片到剪貼簿（例如在瀏覽器中右鍵「複製圖片」）。")
            return
        if isinstance(img, list):
            # 剪貼簿內容是檔案路徑列表
            if not img:
                messagebox.showwarning("剪貼簿沒有圖片", "找不到可用的圖片檔案。")
                return
            self._set_cover_from_path(img[0])
            return
        try:
            img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=92)
            self.cover_bytes = buf.getvalue()
            self.cover_mime = "image/jpeg"
        except Exception as e:
            messagebox.showerror("圖片處理失敗", f"{e}")
            return
        self._refresh_cover_preview()

    def _remove_cover(self):
        self.cover_bytes = None
        self._refresh_cover_preview()

    # ---------- 塞壬唱片搜尋下載 ----------
    def _msr_set_download_status(self, text, color="#0a0"):
        self.download_status_label.config(text=text, foreground=color)

    def _msr_clear_download_fields(self):
        self.msr_selected_song = None
        self.msr_selected_detail = None
        self.msr_selected_album = None
        for var in self.dl_vars.values():
            var.set("")
        self.dl_cover_bytes = None
        self.dl_cover_photo = None
        self.dl_cover_canvas.config(image="", text="（無封面）")

    def _msr_clear_results(self):
        self.msr_matches = []
        self.msr_result_listbox.delete(0, "end")
        self._msr_clear_download_fields()
        self.msr_result_hint.config(text="搜尋塞壬唱片官方曲庫")

    def _msr_refresh_dl_cover_preview(self):
        if not self.dl_cover_bytes:
            self.dl_cover_photo = None
            self.dl_cover_canvas.config(image="", text="（無封面）")
            return
        try:
            img = Image.open(io.BytesIO(self.dl_cover_bytes))
            img.thumbnail((COVER_BOX_SIZE, COVER_BOX_SIZE))
            self.dl_cover_photo = ImageTk.PhotoImage(img)
            self.dl_cover_canvas.config(image=self.dl_cover_photo, text="")
        except Exception:
            self.dl_cover_photo = None
            self.dl_cover_canvas.config(image="", text="（無法預覽此圖片）")

    def _msr_fetch_album_cached(self, album_cid):
        if not album_cid:
            return None
        cached = self.msr_album_name_cache.get(album_cid)
        if isinstance(cached, dict):
            return cached
        album = msr_api.fetch_album_detail(album_cid)
        self.msr_album_name_cache[album_cid] = album
        return album

    def _msr_run_search(self):
        """依關鍵字搜尋，結果填入左側清單。"""
        query = self.msr_query_var.get().strip()
        if not query:
            messagebox.showwarning("請輸入關鍵字", "請先在搜尋框輸入歌曲名稱（例如：Renegade）。")
            return

        self._msr_set_download_status("搜尋中...", "#06c")
        self.update_idletasks()
        try:
            if self.msr_song_list is None:
                self.msr_song_list = msr_api.fetch_song_list()
            matches = msr_api.search_songs(query, self.msr_song_list)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self._msr_set_download_status("")
            messagebox.showerror("連線失敗", f"無法連線到塞壬唱片官網，請檢查網路連線：\n{e}")
            return
        except Exception as e:
            self._msr_set_download_status("")
            messagebox.showerror("搜尋失敗", f"{e}")
            return

        if not matches:
            self._msr_clear_results()
            self._msr_set_download_status("")
            messagebox.showinfo(
                "找不到結果",
                f"在塞壬唱片（明日方舟官方音樂庫）找不到「{query}」。\n"
                "可能是關鍵字要再精確一點、不是明日方舟的歌曲，\n"
                "或這首歌（例如部分特別合作曲，像 Eclipse 這種對外合作的）\n"
                "沒有收錄在官網目錄裡，這種情況就只能手動處理了。",
            )
            return

        self.msr_matches = matches
        self.msr_result_listbox.delete(0, "end")
        self._msr_clear_download_fields()

        # 結果不太多時補上專輯名，方便同名曲分辨
        if len(matches) <= 20:
            self._msr_set_download_status("載入專輯名稱中...", "#06c")
            self.update_idletasks()
            for s in matches:
                album_name = ""
                try:
                    album = self._msr_fetch_album_cached(s.get("albumCid") or "")
                    album_name = ((album or {}).get("name") or "").strip()
                except Exception:
                    album_name = ""
                label = f"{s['name']}  —  {album_name}" if album_name else s["name"]
                self.msr_result_listbox.insert("end", label)
        else:
            for s in matches:
                self.msr_result_listbox.insert("end", s["name"])

        self.msr_result_hint.config(text=f"找到 {len(matches)} 筆結果")
        self.msr_result_listbox.selection_set(0)
        self.msr_result_listbox.see(0)
        self._msr_set_download_status("")
        self._on_select_msr_result()

    def _on_select_msr_result(self, _event=None):
        sel = self.msr_result_listbox.curselection()
        if not sel or sel[0] >= len(self.msr_matches):
            return
        song = self.msr_matches[sel[0]]
        self._msr_load_preview(song)

    def _msr_load_preview(self, song):
        """選取左側歌曲後，抓取官方資料填到右側欄位與封面。"""
        self.msr_selected_song = song
        self.msr_selected_detail = None
        self.msr_selected_album = None
        self._msr_set_download_status("載入資料中...", "#06c")
        self.update_idletasks()

        try:
            detail = msr_api.fetch_song_detail(song["cid"])
            album = self._msr_fetch_album_cached(song.get("albumCid") or "")
            cover_url = (album or {}).get("coverUrl") or ""
            cover_bytes = None
            if cover_url:
                req = urllib.request.Request(cover_url, headers=msr_api.HEADERS)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    cover_bytes = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self._msr_set_download_status("")
            messagebox.showerror("連線失敗", f"無法下載專輯資料/封面：\n{e}")
            return
        except Exception as e:
            self._msr_set_download_status("")
            messagebox.showerror("抓取失敗", f"{e}")
            return

        self.msr_selected_detail = detail
        self.msr_selected_album = album or {}

        album_name = (self.msr_selected_album.get("name") or "").strip()
        # artists 以歌曲 detail 為準；沒有再退回列表項目或專輯內曲目的 artistes
        artists = (
            (detail or {}).get("artists")
            or song.get("artists")
            or song.get("artistes")
            or []
        )
        self.dl_vars["title"].set(song["name"])
        self.dl_vars["album"].set(album_name)
        self.dl_vars["albumartist"].set("塞壬唱片-MSR")
        self.dl_vars["artist"].set(msr_api.format_artists(artists))
        self.dl_vars["genre"].set("Game")
        self.dl_vars["composer"].set("")  # 官方 API／公開可靠來源都沒有作曲欄
        self.dl_vars["tracknumber"].set("")
        date = msr_api.guess_date_from_cover_url(cover_url)
        self.dl_vars["date"].set(date)

        self.dl_cover_bytes = cover_bytes
        self.dl_cover_mime = "image/jpeg"
        self._msr_refresh_dl_cover_preview()

        has_source = bool((detail or {}).get("sourceUrl"))
        has_lyrics = bool((detail or {}).get("lyricUrl"))
        if has_source and has_lyrics:
            self._msr_set_download_status("已帶入官方資料（含歌詞），可直接下載或先修改欄位", "#0a0")
        elif has_source:
            self._msr_set_download_status("已帶入官方資料（此曲官方無歌詞），可直接下載", "#0a0")
        else:
            self._msr_set_download_status("已帶入資料，但官方沒有音源連結", "#c60")

    def _msr_download_new_song(self):
        song = self.msr_selected_song
        if song is None:
            messagebox.showwarning("尚未選擇歌曲", "請先搜尋，再從左側清單選擇要下載的歌曲。")
            return

        detail = self.msr_selected_detail
        album = self.msr_selected_album or {}
        source_url = (detail or {}).get("sourceUrl") or ""

        self._msr_set_download_status("下載音源中...", "#06c")
        self.update_idletasks()
        try:
            if not source_url:
                detail = msr_api.fetch_song_detail(song["cid"])
                self.msr_selected_detail = detail
                source_url = detail.get("sourceUrl") or ""
            if not source_url:
                raise RuntimeError("官方資料庫裡沒有這首歌的音源連結。")
            if not album:
                album = self._msr_fetch_album_cached(song.get("albumCid") or "") or {}
                self.msr_selected_album = album

            req = urllib.request.Request(source_url, headers=msr_api.HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                audio_bytes = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self._msr_set_download_status("")
            messagebox.showerror("下載失敗", f"請檢查網路連線：\n{e}")
            return
        except Exception as e:
            self._msr_set_download_status("")
            messagebox.showerror("下載失敗", f"{e}")
            return

        cover_bytes = self.dl_cover_bytes
        cover_url = album.get("coverUrl") or ""
        if cover_bytes is None and cover_url:
            try:
                req2 = urllib.request.Request(cover_url, headers=msr_api.HEADERS)
                with urllib.request.urlopen(req2, timeout=15) as resp2:
                    cover_bytes = resp2.read()
            except Exception:
                cover_bytes = None

        src_ext = os.path.splitext(urllib.parse.urlparse(source_url).path)[1].lower()
        final_ext = src_ext

        if src_ext == ".wav" and self.convert_wav_to_flac_var.get():
            self._msr_set_download_status("轉檔成 FLAC 中...", "#06c")
            self.update_idletasks()
            try:
                audio_bytes = wav_bytes_to_flac_bytes(audio_bytes)
                final_ext = ".flac"
            except Exception as e:
                self._msr_set_download_status("")
                messagebox.showerror("轉檔失敗", f"{e}")
                return
        elif src_ext not in (".mp3", ".flac", ".wav"):
            messagebox.showwarning(
                "不認得的格式",
                f"官方音源副檔名是「{src_ext}」，會直接存下來，但可能無法正確寫入標籤。",
            )

        values = {k: var.get().strip() for k, var in self.dl_vars.items()}
        album_name = values.get("album") or (album.get("name") or "").strip()
        title = values.get("title") or song["name"]
        base_name = f"{album_name} - {title}" if album_name else title
        suggested_name = msr_api.sanitize_filename(base_name) + final_ext

        want_embed = self.embed_lyrics_var.get()
        want_sidecar = self.save_lyrics_file_var.get()
        lyrics_text = None
        lyrics_source = None
        if (want_embed or want_sidecar) and not looks_instrumental(title=title):
            self._msr_set_download_status("搜尋歌詞中（官方 + LRCLIB）...", "#06c")
            self.update_idletasks()
            try:
                lyrics_text, lyrics_source, _score = pick_best_lyrics(
                    title=title,
                    artist=values.get("artist") or msr_api.format_artists((detail or {}).get("artists") or []),
                    album=album_name,
                    msr_song=song,
                    msr_detail=detail,
                )
            except Exception as e:
                lyrics_text = None
                lyrics_source = None
                messagebox.showwarning("歌詞下載失敗", f"音檔仍會繼續下載，但這次沒抓到歌詞：\n{e}")

        self._persist_settings()
        self._msr_set_download_status("")
        save_path = filedialog.asksaveasfilename(
            title="選擇要存檔的位置與檔名",
            initialdir=self.current_dir,
            initialfile=suggested_name,
            defaultextension=final_ext,
            filetypes=[(final_ext.upper().lstrip(".") + " 檔案", f"*{final_ext}"), ("所有檔案", "*.*")],
        )
        if not save_path:
            return

        # 若使用者另選了資料夾，之後以此為音樂資料夾
        save_dir = os.path.dirname(os.path.abspath(save_path))
        if os.path.isdir(save_dir) and os.path.normcase(save_dir) != os.path.normcase(self.current_dir):
            self.current_dir = save_dir
            self.music_dir_var.set(self.current_dir)
            self._persist_settings()

        embed_lyrics_text = lyrics_text if (want_embed and lyrics_text) else None
        try:
            with open(save_path, "wb") as f:
                f.write(audio_bytes)

            if want_sidecar and lyrics_text:
                lrc_path = os.path.splitext(save_path)[0] + ".lrc"
                with open(lrc_path, "w", encoding="utf-8", newline="\n") as lf:
                    lf.write(lyrics_text if lyrics_text.endswith("\n") else lyrics_text + "\n")

            lower_path = save_path.lower()
            if lower_path.endswith(".flac"):
                save_flac_tags(
                    save_path, values, cover_bytes, self.dl_cover_mime or "image/jpeg", embed_lyrics_text
                )
            elif lower_path.endswith(".mp3"):
                save_mp3_tags(
                    save_path, values, cover_bytes, self.dl_cover_mime or "image/jpeg", embed_lyrics_text
                )
            elif lower_path.endswith(".wav"):
                tip = "音檔已存成 wav。若之後要寫入標籤／嵌入歌詞，建議先轉成 FLAC。"
                if want_sidecar and lyrics_text:
                    tip += "\n獨立歌詞檔（.lrc）已一併寫出。"
                messagebox.showinfo("已儲存 wav", tip)
        except Exception as e:
            messagebox.showerror(
                "寫入標籤失敗",
                f"音檔已經存下來了，但寫入標籤時失敗：\n{e}\n\n檔案位置：{save_path}",
            )
            self._reload_file_list()
            return

        self._reload_file_list()
        if save_path in self.files:
            idx = self.files.index(save_path)
            self.file_listbox.selection_clear(0, "end")
            self.file_listbox.selection_set(idx)
            self.file_listbox.see(idx)
            self._load_file(save_path)

        lyric_bits = []
        if embed_lyrics_text:
            lyric_bits.append("已嵌入")
        if want_sidecar and lyrics_text:
            lyric_bits.append("已存 .lrc")
        if lyric_bits:
            self._msr_set_download_status(
                f"已下載 ✓ 歌詞：{'、'.join(lyric_bits)}（來源：{lyrics_source}）", "#0a0"
            )
        else:
            self._msr_set_download_status("已下載 ✓ 可到「標籤編輯」再核對欄位", "#0a0")
        self.after(5000, lambda: self._msr_set_download_status(""))

    # ---------- 儲存 ----------
    def _save_current(self):
        if not self.current_file:
            messagebox.showwarning("尚未選擇檔案", "請先在左側清單選擇一首歌曲。")
            return

        ext = os.path.splitext(self.current_file)[1].lower()
        values = {k: var.get().strip() for k, var in self.vars.items()}

        try:
            if ext == ".flac":
                self._save_flac(values)
            else:
                self._save_mp3(values)
        except Exception as e:
            messagebox.showerror("儲存失敗", f"{e}")
            return

        self.status_label.config(text="已儲存 ✓")
        idx = self.files.index(self.current_file)
        self.after(1500, lambda: self.status_label.config(text=""))

    def _save_flac(self, values):
        save_flac_tags(self.current_file, values, self.cover_bytes, self.cover_mime)

    def _save_mp3(self, values):
        save_mp3_tags(self.current_file, values, self.cover_bytes, self.cover_mime)


if __name__ == "__main__":
    app = SirenApp()
    app.mainloop()
