# android-telegram-agent

Revision 1；2026-10-11。

Lightweight Telegram bridge for AI CLI agents on Android via Termux.

## 狀態

目前是專案初始化，尚未提供可執行的 Bot。此提交只有文件、授權、忽略規則與假值設定範例；不含第三方 CLI 或任何帳號憑證。

目標是一般 Android 裝置上的非 root Termux 環境。初始驗證目標為 Android 9／API 28／ARM64；Telegram 完整往返及背景可靠性待實作與驗證。

初期僅實作 AGY；其他 CLI 屬未來獨立評估，不在首階段範圍，也不預建多工具路由或插件框架。

## 第一階段範圍

- Android 主動使用 Telegram Bot API long polling，無須對外開啟接收埠。
- 授權的 Telegram 使用者傳送文字；Bot 按需啟動官方 `agy`，把真實模型回答回傳原聊天。
- 只允許一項 AGY 任務；忙碌時回覆狀態，不建立複雜工作佇列。
- 以使用者與聊天的成對白名單檢查授權，未授權訊息不得啟動 AGY。
- 最小指令：`/start`、`/help`、`/status`、`/cancel`。
- 處理錯誤、逾時、取消、長回覆分段、網路恢復及子程序清理。
- 使用自有工作目錄與限定工具權限；不提供任意遠端 shell、root 操作或全面跳過核准。

## 預定設定

`config.example.json` 是未來實作的設定契約草案，不是目前可用的啟動設定。所有值都是占位符，後續程式須驗證路徑、數值與白名單。

- 新建專用 Telegram Bot；實際 token 與白名單只存於 Termux 私有儲存空間、Git 工作目錄之外，檔案權限設為 `0600`。
- 安裝官方 AGY，先在同一 Termux App 身分完成 Google 登入，再驗證單次 JSON 請求。
- 實際設定不得提交；例子中的路徑與識別碼不能直接當生產設定使用。
- 不提交對話、實機紀錄、截圖、裝置識別資訊、私有連結或第三方執行檔。
- 每次推送前須掃描追蹤檔案與完整 Git 歷史，包括提交訊息和作者資訊；`.gitignore` 不能清除已提交的敏感內容。

## CLI 整合注意事項

- 初版使用 `agy -p ... --output-format json`；後續可評估以指定 conversation ID 接續對話。
- exit code 0 或 `SUCCESS` 不保證工具操作成功；必須檢查錯誤、拒絕清單與實際結果。
- 串流模式不支援 `control_request`／`control_response`；不可將它當作工具核准協議。
- CLI 與 Telegram 都需要服務連線；本專案不提供離線模型。

## 官方參考

- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Termux](https://github.com/termux/termux-app)
- [Antigravity CLI Headless](https://www.antigravity.google/docs/cli/headless/)
- [Antigravity CLI 權限](https://www.antigravity.google/docs/permissions?tab=cli)

## 授權

原創專案內容採 MIT；Google Antigravity、Telegram 與 Termux 依各自條款及授權提供。本儲存庫不重新發行它們的程式或憑證。

## 術語白話解釋

- Bot／CLI／AGY／Termux：聊天機器人／命令介面／Google AI 命令工具／Android 終端機 App。
- API／long polling／token：服務呼叫介面／由裝置持續等待新訊息的取件方式／機器人登入憑證。
- root／shell／0600：最高管理權限／執行命令的程式／只有檔案持有人可讀寫的權限。
- JSON／conversation ID／串流：程式文字資料格式／對話識別碼／逐筆傳送資料。
- Git／追蹤檔案／提交：版本管理工具／納入版本管理的檔案／保存的一次版本紀錄。
- API 28／ARM64／MIT：Android 9 系統介面版本／處理器架構／允許再利用且保留聲明的授權。
