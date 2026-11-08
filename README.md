# 賽任唱片下載器（MSR Downloader）

明日方舟「塞壬唱片」官方音樂庫的本機工具：搜尋下載、寫入標籤／封面、嵌入或另存歌詞，也可編輯已下載的 MP3／FLAC。

## 功能

- **標籤編輯**：瀏覽音樂資料夾、改 metadata、換封面、儲存（API／輸入原文照存）
- **搜尋下載**：搜尋官方曲庫、預覽標籤與封面後下載
- **wav → FLAC**：下載時可選（需本機 `ffmpeg`）
- **歌詞**：官方 `lyricUrl` + [LRCLIB](https://lrclib.net)，自動評分選較佳
  - **嵌入歌詞**：寫入音檔（FLAC `LYRICS`／MP3 `USLT`）
  - **獨立歌詞檔**：另存同名 `.lrc`
- **音樂資料夾**：視窗上方可直接改路徑或瀏覽；會記在 `settings.json`

## 需求

```text
Python 3.10+
pip install mutagen pillow
```

下載官方 wav 並轉 FLAC 時需要 [ffmpeg](https://ffmpeg.org/)，並加入 `PATH`。

## 啟動

雙擊 `賽任唱片下載器.bat`，或：

```bat
pythonw siren_app.py "C:\path\to\music_folder"
```

未傳路徑時，預設使用上一層資料夾（例如本專案放在 `賽任唱片收藏\MSR_downloader` 時，音樂目錄為 `賽任唱片收藏`）。也可在 App 上方隨時更改。

## 檔名與標籤慣例

- 建議檔名：`專輯 - 標題.ext`（內容與官方／輸入一致，不做繁簡轉換）
- Artist：官方 `artists`（多數為 `塞壬唱片-MSR`，少數含合作者）
- Album Artist：`塞壬唱片-MSR`
- Genre：`Game`
- Composer：官方無資料，預設空白

## 專案結構

```text
MSR_downloader/
  siren_app.py          # GUI 主程式
  msr_api.py            # 塞壬官方 API
  lyrics_sources.py     # 歌詞多來源與評分
  settings.json         # 本機設定（可忽略進版控）
  賽任唱片下載器.bat
  README.md
```

## 授權與來源

- 音源與封面來自 [塞壬唱片](https://monster-siren.hypergryph.com/) 公開 API，請遵守官方使用條款，僅供個人收藏。
- 歌詞另可能來自 LRCLIB 社群資料庫。
