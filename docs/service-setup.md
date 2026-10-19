# Termux 版本化部署與常駐服務

Revision: 1 — 2026-10-19

本文件適用於非 root Termux。私人 config、Token、AGY 登入資料與 workspace
不得放入 Git checkout 或 release 目錄。先依
[`termux-setup.md`](termux-setup.md) 完成前景驗收，再建立常駐服務。

## 版本化安裝

先取得公開 repository，並選擇已 review、已在 `origin/main` 的完整 commit SHA：

```sh
git clone https://github.com/antidark0605/android-telegram-agent.git
cd android-telegram-agent
git fetch --prune origin main
APPROVED_COMMIT="填入已核對的完整40字元commit SHA"
git merge-base --is-ancestor "$APPROVED_COMMIT" origin/main
scripts/install-termux.sh \
  --ref "$APPROVED_COMMIT" \
  --config "$HOME/.config/android-telegram-agent/config.json"
```

`git merge-base` exit 0 只證明 commit 位於已取得的 `origin/main` 歷史；仍須由
release／review 紀錄確認核准版本。安裝工具使用 `git archive`，在
`$HOME/opt/android-telegram-agent/releases/<commit>` 建立不含 `.git` 的版本，執行
完整 unittest 與 compile，驗證私人 config 後才原子切換 `current` symlink。
既有版本留在 `releases/`；工具不複製或修改 config、workspace、Token、AGY 認證，
也不啟停服務。

## 升級與回滾

升級前先在 source checkout 取得遠端狀態並核對版本：

```sh
git fetch --prune origin main
APPROVED_COMMIT="填入新的完整40字元commit SHA"
git merge-base --is-ancestor "$APPROVED_COMMIT" origin/main
scripts/upgrade-termux.sh \
  --ref "$APPROVED_COMMIT" \
  --config "$HOME/.config/android-telegram-agent/config.json"
```

`upgrade-termux.sh` 刻意不執行 `git fetch` 或 `git pull`，避免下載與啟用混成一個
不可審查步驟。它只接受完整 SHA，並沿用 installer 的驗證與切換流程。

切回上一個已安裝且有版本 marker 的 release：

```sh
scripts/rollback-termux.sh \
  --config "$HOME/.config/android-telegram-agent/config.json"
```

回滾先 compile 上一版並以該版程式驗證目前 config，再切換 `current`／`previous`。
它不回復或覆蓋私人 session state；若 config schema 曾不相容，驗證失敗會在切換前停止。

## Doctor

離線檢查不執行 AGY、不輪詢 Telegram：

```sh
python scripts/doctor.py \
  --checkout "$HOME/opt/android-telegram-agent/current" \
  --config "$HOME/.config/android-telegram-agent/config.json"
```

額外讀取 Telegram Bot 身分與命令選單，但不呼叫 `getUpdates`：

```sh
python scripts/doctor.py \
  --checkout "$HOME/opt/android-telegram-agent/current" \
  --config "$HOME/.config/android-telegram-agent/config.json" \
  --network
```

輸出只有固定的 `*_OK`／`*_FAILED` 狀態，不含 Token、ID、路徑、hostname、API
response 或 AGY 內容。`AGY_EXECUTABLE_OK` 只驗證 executable 存在；真實 JSON、登入、
模型與 permissions 仍須依 `termux-setup.md` 個別驗收。

## termux-services

官方 `termux-services` 使用 runit 管理常駐程序：

```sh
pkg install termux-services
```

安裝後依官方要求重新開啟 Termux shell，讓 service daemon 啟動。建立服務：

```sh
SERVICE_DIR="$PREFIX/var/service/android-telegram-agent"
LOG_DIR="$PREFIX/var/log/sv/android-telegram-agent"
mkdir -p "$SERVICE_DIR/log" "$LOG_DIR"
cat >"$SERVICE_DIR/run" <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
exec 2>&1
exec "$PREFIX/bin/python" "$HOME/opt/android-telegram-agent/current/bot.py" \
  --config "$HOME/.config/android-telegram-agent/config.json"
EOF
cat >"$SERVICE_DIR/log/run" <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
exec "$PREFIX/bin/svlogd" -tt "$PREFIX/var/log/sv/android-telegram-agent"
EOF
chmod 700 "$SERVICE_DIR/run" "$SERVICE_DIR/log/run"
sv-enable android-telegram-agent
sv up android-telegram-agent
sv status android-telegram-agent
```

只允許一個程序使用同一 Bot Token。升級或回滾後用 `sv restart
android-telegram-agent` 載入新版本，並執行 doctor、`/status` 與真實往返驗收。
不要同時保留舊的 shell、nohup、tmux 或其他 service poller。

## 開機啟動

需要重開機自動啟動時才安裝 Termux:Boot；Termux 與所有 plugin 必須來自相同簽章
來源。依官方方式先手動開啟 Termux:Boot 一次，再讓 boot script 載入
`$PREFIX/etc/profile.d/start-services.sh`。是否使用 wake lock 取決於裝置實測；它會增加
耗電，不列為 installer 預設。

來源：

- [Termux Installation](https://github.com/termux/termux-app#installation)
- [termux-services](https://github.com/termux/termux-services)
- [Termux:Boot](https://github.com/termux/termux-boot)

## 與既有測試部署的差異

| 項目 | 既有手動測試流程 | 本文件建議流程 |
| --- | --- | --- |
| 程式位置 | 直接從一個 checkout 啟動 | 每個 commit 獨立 release，`current` 指向核准版 |
| 版本選擇 | 依當下 checkout | 完整 commit SHA |
| 切換前驗證 | 操作者手動執行 | installer 強制 compile、完整 unittest、選用 config 驗證 |
| 更新 | 手動拉取與重啟 | 下載、核對、安裝、重啟分成可檢查步驟 |
| 回復 | 手動找回舊 checkout | `previous` 指向上一版，rollback 驗證後切換 |
| 常駐 | 既有裝置專用啟動方式 | 公開、可重現的 termux-services runit 範本 |
| 診斷 | 查看私人事件與個別命令 | doctor 固定狀態碼，不輸出秘密；原始證據仍留私人位置 |
| 私人設定 | checkout 外 | 相同；工具永不覆蓋 |
| YOLO | 由既有私人 config 決定 | 完全相同；工具不修改，公開範例仍為 `false` |
| Bot Token | 既有測試 Bot | 每台 active 裝置使用專用 Token，禁止雙 poller |

## 術語白話解釋

- release／commit SHA／marker：一份已安裝程式版本／精確版本編號／記錄該編號的本機檔案。
- symlink／原子切換：指向另一位置的檔案捷徑／不留下半完成狀態的單一步驟替換。
- compile／unittest：檢查 Python 可解析／以合成資料自動驗證功能。
- doctor／schema：固定輸出的安裝診斷工具／設定資料的欄位與格式規則。
- runit／poller：監督常駐服務的工具／持續向 Telegram 取訊息的程序。
- wake lock：要求 Android 暫時不要讓 CPU 休眠的機制，會增加耗電。
