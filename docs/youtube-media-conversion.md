# 非 root Termux 的 YouTube 影音轉換

Revision: 3 — 2026-10-13

此文件記錄可重現的裝置端流程，適用於有權下載的公開、非直播影片。媒體下載是 Bot 的選用能力，不是原專案 Python 運行依賴。原版 Bot 只回文字；本文件不宣稱原版已具備自動附檔傳送。實際裝置與聊天資訊、憑證和原始紀錄不存於專案。

## 範圍與驗證界線

- 以下是選用工具的安裝與檢查流程；Python Bot 本身不需要影音依賴，也沒有公開的自動附檔功能。
- 逐一確認下載、轉檔、解碼及傳送結果；沒有完成的階段標 NOT VERIFIED，不把模型回覆當檔案成功。
- MP4 指令依官方選項提供，須另行驗證實際影片與裝置相容性。
- 不使用 root、修改 Android 系統或繞過登入、付費、DRM 等存取限制。
- 網路錯誤、影片無法存取或缺少格式不能靠加長期限解決。

## 1. 準備 Termux 套件

在 Termux App 的一般使用者終端執行，先確認 `id -u` 非 0。沿用已有環境，勿修改 PATH 或 shell 啟動檔。

```sh
id -u
apt-get -s --no-install-recommends install python python-pip ffmpeg quickjs
```

閱讀模擬結果的新增、升級與移除清單後，才安裝需要的套件：

```sh
apt-get --no-install-recommends install python python-pip ffmpeg quickjs
python --version
ffmpeg -version
ffprobe -version
qjs --version
```

套件名稱與實際版本以使用中的官方套件庫為準。只有版本命令真的能執行才算完成；套件存在、安裝命令 exit 0 都不是充足證據。來源：[Termux packages](https://github.com/termux/termux-packages)、[yt-dlp EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS)。

若有 `CANNOT LINK EXECUTABLE`，先核對缺少的符號及套件相容性，不能只看安裝退出碼；不要未核實就全域升級。來源：[Termux dynamic library linking errors](https://github.com/termux/termux-packages/wiki/Termux-execution-environment#dynamic-library-linking-errors)。

## 2. 在私人目錄安裝下載工具

用獨立目錄，避免更動 Bot 原來的 Python 依賴；以下範例不含真實識別或憑證。

```sh
umask 077
MEDIA_ROOT="$HOME/.local/share/android-telegram-agent/media"
mkdir -p "$MEDIA_ROOT/python"
chmod 700 "$MEDIA_ROOT"
python -m pip install --target "$MEDIA_ROOT/python" 'yt-dlp[default]'
cat > "$MEDIA_ROOT/run-yt-dlp.py" <<'PY'
# Revision: 1 — isolated yt-dlp entry point.
import runpy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'python'))
runpy.run_module('yt_dlp', run_name='__main__')
PY
chmod 600 "$MEDIA_ROOT/run-yt-dlp.py"
python "$MEDIA_ROOT/run-yt-dlp.py" --version
```

更新時重新核對 yt-dlp 與 EJS 相容版本。QuickJS 須明確啟用；不要因為裝了 Node.js 就假設它在舊套件環境一定可執行。此流程選用 QuickJS；仍須核對當次 yt-dlp/EJS 支援的 runtime 與版本。來源：[yt-dlp installation/dependencies](https://github.com/yt-dlp/yt-dlp#dependencies)、[EJS version requirements](https://github.com/yt-dlp/yt-dlp/wiki/EJS)。

## 3. 分開驗證影片資訊、下載和轉檔

將 `VIDEO_URL` 在私人終端設定為實際公開影片網址，不寫進 repo。输出目錄必須是允許的工作目錄子目錄。

```sh
OUTPUT_DIR="$HOME/telegram-agent-workspace/media"
mkdir -p "$OUTPUT_DIR"
chmod 700 "$OUTPUT_DIR"
python "$MEDIA_ROOT/run-yt-dlp.py" --ignore-config --no-playlist \
  --js-runtimes quickjs --socket-timeout 20 --retries 2 \
  --dump-single-json --skip-download "$VIDEO_URL"
```

資訊 JSON 含影片資料與媒體 URL，只存私人地方。先檢查可存取、非直播、時長和容量符合本次範圍，再下載。

MP3：

```sh
python "$MEDIA_ROOT/run-yt-dlp.py" --ignore-config --no-playlist \
  --js-runtimes quickjs --socket-timeout 20 --retries 2 --fragment-retries 2 \
  -x --audio-format mp3 --audio-quality 128K \
  -o "$OUTPUT_DIR/output.%(id)s.%(ext)s" "$VIDEO_URL"
```

MP4（官方選項，尚未列本次實機 PASS）：

```sh
python "$MEDIA_ROOT/run-yt-dlp.py" --ignore-config --no-playlist \
  --js-runtimes quickjs --socket-timeout 20 --retries 2 --fragment-retries 2 \
  -t mp4 -f 'bv*[height<=480]+ba/b[height<=480]' \
  -o "$OUTPUT_DIR/output.%(id)s.%(ext)s" "$VIDEO_URL"
```

不同影片用各自 ID 命名，避免把前一部影片的同名輸出誤認為本次成功。來源：[yt-dlp options](https://github.com/yt-dlp/yt-dlp#usage-and-options)。

## 4. AGY 與 Bot 的期限和工具權限

下載、解決 JavaScript 驗證、轉檔與模型啟動需要不同時間。不能只把單次任务期限加大，然後忽略底層工具錯誤。

- 私人 config 的 `task_timeout_seconds` 單位為秒，可設15–86400或`null`；`null`僅取消Python外層期限。用`config.load_settings`驗證，別把它當AGY內部也無期限。
- AGY 自身 headless 期限也要配合，例如 `--print-timeout 15m`，不是只調 Bot 外層。
- 公開範本`yolo=false`。`--dangerously-skip-permissions`會自動批准工具操作，只能在持有人明確授權後使用；安裝影音依賴不需要自動放寬AGY權限。
- 给 AGY 已驗證的精確下載命令或有階段記錄的私人 helper，避免在單一請求反覆安裝、探索或修改設定。
- 以裝置實際 AGY 與既有非 root 啟動方式執行，不以電腦端成功代替 Android 結果。

來源：[`config.py`](../config.py)、[`agy.py`](../agy.py)、[AGY headless](https://www.antigravity.google/docs/cli/headless/)。

## 5. 成功條件與診斷紀錄

請在私人地方分開記錄 metadata、download、ffprobe 的退出碼、耗時與错误，以及 AGY 的實際 JSON/denied_actions。模型說完成、`status=SUCCESS` 或 exit 0 本身不算檔案成功。

將 `OUTPUT_FILE` 設為本次實際產生的檔案後執行：

```sh
ffprobe -v error -show_entries format=duration,format_name:stream=codec_name,codec_type \
  -of json "$OUTPUT_FILE"
```

MP3 通過須檔案非空、可解析、含 MP3 音訊，且時長符合來源；MP4 另須有影片與音訊串流。再核對 AGY 無未解決工具拒絕，及 Telegram 回覆路由。若檔案仍只存在裝置，不能宣稱已回傳給使用者；附檔須另核對傳送結果。私人部署可使用 `sendDocument` 的 multipart 上傳；回傳是 Message，應檢查實際的 document/audio/video 欄位與原 chat，而非假設一定只有 document。伺服器接受上傳不代表使用者已看到；使用者端交付須另驗證。原公開 Bot 沒有這個傳送方法，不能把私人部署的 helper 當成已合併公開功能。來源：[ffprobe](https://ffmpeg.org/ffprobe.html)、[`agy.py`](../agy.py)、[`telegram_api.py`](../telegram_api.py)、[Telegram sendDocument](https://core.telegram.org/bots/api#senddocument)。

## 術語白話解釋

- yt-dlp／FFmpeg／ffprobe：下載影音的工具／轉檔工具／檢查影音內容的工具。
- QuickJS／EJS：執行 JavaScript 的工具／yt-dlp 用於處理 YouTube JavaScript 驗證的程式。
- libc++／linker：C++ 程式使用的共用函式庫／載入執行檔與函式庫的機制。
- metadata／串流：影片資訊／檔案內的音訊或影片內容。
- helper／YOLO：固定執行流程的小程式／自動批准AGY工具操作的模式。
- JSON／PATH／exit code：結構化資料／尋找指令的目錄清單／程序結束結果代碼。
