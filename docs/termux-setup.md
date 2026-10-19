# Termux 依賴與安裝

Revision: 4 — 2026-10-19

適用於 `antidark0605/android-telegram-agent` 的 AGY 對話版本。以下命令在 Android **Termux App 前景、一般使用者**執行，不在電腦或 root ADB shell 執行。本文是安裝指南，不授權變更已有服務或認證。

## 依賴

| 項目 | 用途與取得方式 |
| --- | --- |
| Termux App | Android 命令列環境；從官方列出的 F-Droid 或 GitHub Releases 來源取得 |
| Python | 執行 Bot 與測試；使用 Termux 官方 `python` 套件，建議 3.11 以上；既有 CI 涵蓋 3.11–3.13 |
| Git | 拉取專案；已有純原始碼包時不必額外安裝 Git |
| 官方 AGY CLI | 呼叫模型；須有符合裝置架構且能在 Android/Termux 執行的官方版本，並在同一使用者完成登入 |
| 專用 Telegram Bot | 私下取得 Bot Token 與允許的 user/chat 配對；同一 Token 僅供一個輪詢程序使用 |
| 網際網路 | 連線 Telegram 與 AGY 模型服務；由裝置主動連線 |

專案 Python 模組只使用標準函式庫，沒有需要 `pip install` 的第三方套件。`python-pip`、Termux:API、Termux:Boot、Termux:Tasker 皆不列為本版本依賴。來源：[`bot.py`](../bot.py)、[`agy.py`](../agy.py)、[`config.py`](../config.py)、[`telegram_api.py`](../telegram_api.py)、[CI](../.github/workflows/python-tests.yml)。

## 1. 安裝與確認 Termux

依 [官方 Installation](https://github.com/termux/termux-app#installation) 選擇來源並安裝 App。已有 Termux 時沿用既有來源；App 與附加元件不能混用不同來源的簽章。不要為本指南移除或替換既有環境。官方完整套件支援要求 Android 7 以上；這不等同 AGY 在所有 Android 版本都已驗證。

開啟 Termux；已有工作時從側邊欄選 **NEW SESSION** 建立獨立前景終端。先執行：

```sh
id
command -v python
command -v git
```

`id` 的 UID 必須非 0；若為 root，停止安裝與測試。不要使用 `su` 繞過。前景新終端入口來源：[TermuxActivity#setNewSessionButtonView](https://github.com/termux/termux-app/blob/v0.118.3/app/src/main/java/com/termux/app/TermuxActivity.java)。

## 2. 安裝 Python 與必要套件

以下僅在允許安裝套件的環境執行；管理中的裝置須遵守其授權邊界。

```sh
pkg update
apt-get -s --no-install-recommends install python git
apt-get --no-install-recommends install python git
python --version
git --version
```

先閱讀模擬安裝的新增、升級、移除清單，再確認實際安裝。只取得 Python/Git 與套件管理器判定的必要依賴；已有 Git 或不需要 Git 時，兩條 `apt-get` 命令都移除 `git`。不加全域 `upgrade`，不略過套件驗證。`pkg update` 更新套件索引；`-s` 不實際安裝；`--no-install-recommends` 不額外安裝建議套件。來源：[Termux Package Management](https://github.com/termux/termux-packages/wiki/Package-Management)、`apt-get --help`、[Python 套件定義](https://github.com/termux/termux-packages/blob/master/packages/python/build.sh)。

Python 的相依清單可能隨官方版本更新，應以當次模擬結果為準；不能只根據命令退出碼判斷完成。以下檢查確認 Bot 所需模組實際可載入：

```sh
python -c 'import json, ssl, urllib.request, subprocess, threading; print("PYTHON_OK")'
```

通過條件：版本符合選用要求，最後一條輸出 `PYTHON_OK` 且沒有載入錯誤。

## 3. 準備 AGY

參照 [官方 Installation and auth](https://www.antigravity.google/docs/cli/install/)。該頁的通用安裝腳本以 macOS/Linux 等平台為對象，**本文未驗證它在 Android 的安裝流程**；不要將 Linux 套件視為 Android 相容的證據。本專案不附帶 AGY binary；在 Android 安裝方式未核實前，沿用已核實的官方 Android 可執行檔，缺少時記 NOT VERIFIED。

若既有 `agy` 已能透過 `command -v agy` 找到，在同一非 root Termux 使用者依官方流程登入，再執行：

```sh
agy -p "Reply exactly CLI_OK. Do not use tools." --output-format json
```

若執行檔不在現有 PATH，使用已核實的絕對路徑執行，並於私人 config 的 `agy_command` 填相同路徑；不為此修改 PATH。通過條件：JSON 明確 `status=SUCCESS`、`response` 非空且符合請求，沒有 `error` 或 `denied_actions`；exit 0 本身不算通過。來源：[Headless mode](https://www.antigravity.google/docs/cli/headless/)、[`agy.py`](../agy.py)。此無工具請求不驗證檔案或命令權限。

## 4. 拉取專案與合成測試

```sh
git clone https://github.com/antidark0605/android-telegram-agent.git
cd android-telegram-agent
python -m unittest discover -s tests -v
python -m compileall -q .
```

此命令取得預設分支；審查中的版本須另核對指定 PR 的最新提交，不假設預設分支已含該版本。使用已存在的 checkout 時保留未提交檔案，另建隔離目錄。合成測試顯示 `OK`、編譯無錯誤才通過；測試使用假程序與訊息，不能當真實 Telegram 往返 PASS。來源：[README Tests](../README.md#tests-and-acceptance)、[`tests/`](../tests/)。

## 5. 私有設定與啟動

在專案目錄執行；若下面私人 config 已存在，保留它，不重複複製：

```sh
umask 077
mkdir -p "$HOME/.config/android-telegram-agent" "$HOME/telegram-agent-workspace"
chmod 700 "$HOME/.config/android-telegram-agent" "$HOME/telegram-agent-workspace"
if [ ! -e "$HOME/.config/android-telegram-agent/config.json" ]; then
  cp config.example.json "$HOME/.config/android-telegram-agent/config.json"
fi
chmod 600 "$HOME/.config/android-telegram-agent/config.json"
```

依 [README Configure privately](../README.md#private-configuration) 在私人檔案填入 `bot_token`、`allowed_pairs` 的 user/chat 整數配對、`agy_command` 與 `workspace` 的實際绝對路徑；`task_timeout_seconds` 單位為秒，允許 15–86400；預設 `null` 表示沒有 Python 外層期限。AGY 自身仍受 `print_timeout` 控制，公開範本為 `24h`。`yolo` 預設 `false`；不要為安裝自動開啟。來源：[`config.py`](../config.py)、[`config.example.json`](../config.example.json)。

設定檔與 workspace 必須分離並在 checkout 外；workspace 是同一 Termux home 的子目錄，不能是 home 本身。config 為同使用者擁有的普通檔案，不用符號連結；config 權限 0600、私人目錄 0700。只在私人地方存憑證，不輸出檔案全文或含 Token 的 API URL。完成後先由設定消費端驗證，不顯示秘密：

```sh
python -c 'from config import load_settings; from pathlib import Path; load_settings(Path.home()/".config/android-telegram-agent/config.json"); print("CONFIG_OK")'
python bot.py --config "$HOME/.config/android-telegram-agent/config.json"
```

`CONFIG_OK` 才表示設定檢查通過；範本假值會被拒絕。Bot 保持前景，先傳 `/start`、`/status`，在目標聊天／Topic 傳 `/session start`、用 `/model` 選定模型，再用 `/format` 選擇每個 Topic 的 rich/classic/raw 模式；raw 表示無格式正文，附件 marker 仍會被安全抽取，不會顯示或洩漏。最後傳新純文字或一個照片／文件附件。`/last` 僅是目前 Topic 的手動重送；不會把模糊網路失敗自動重播成 exactly-once。必須在相同聊天／話題收到真實模型答案，才記往返 PASS；首次启动會捨棄舊排隊訊息。完成本次測試後按 Ctrl+C 停止本次 Bot。來源：[`bot.py`](../bot.py)、[`incoming_media.py`](../incoming_media.py)、[`last_store.py`](../last_store.py)。

## 驗證界線

取消、逾時、busy、未授權拒絕、長訊息分段、同組程序清理、網路恢復與關屏各自記錄結果；沒有實測的項目記 NOT VERIFIED。工作目錄不是檔案系統隔離；AGY 權限須遵循 [官方 permissions](https://www.antigravity.google/docs/permissions?tab=cli)，不使用全域 bypass。真實聊天、原始裝置紀錄與登入資料只留私人位置。詳見 [README 驗證清單](../README.md#tests-and-acceptance)。

## 術語白話解釋

- Termux／root／UID：Android 命令列環境／最高系統權限／程序所屬使用者編號。
- CLI／Bot／long polling：命令列程式／自動訊息帳號／持續向伺服器取新訊息的方式。
- 套件／依賴／pip：可安裝軟體／它需要的其他軟體／Python 額外套件安裝工具。
- PATH／checkout／PR／CI：尋找指令的位置清單／專案程式碼目錄／待審查變更／自動執行測試。
- binary／JSON／Token／API：可執行檔／結構化資料／秘密憑證／程式呼叫服務的介面。
- user/chat 配對／workspace／bypass：允許的使用者與聊天組合／AGY 工作目錄／略過權限限制。
- denied_actions／程序群組／符號連結：遭拒的工具動作／同任務的一組程序／指向另一檔案的捷徑。
- mock／PASS／NOT VERIFIED：以假操作驗證／指定檢查通過／尚未完成實際驗證。
