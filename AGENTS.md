# android-telegram-agent 開發規範

Revision: 1 — 2026-10-21

## 適用範圍

- 本檔適用於整個公開 repository `antidark0605/android-telegram-agent`。
- 開始工作前完整閱讀 `README.md`、`docs/termux-setup.md`、
  `docs/service-setup.md`，以及與變更相關的程式與測試。
- 初期產品範圍是官方 AGY、非 root Termux、Python 標準函式庫 Bot、Telegram
  long polling、每次僅一個 AGY 任務。Codex、Claude 或其他 agent adapter 不在目前範圍。
- 工作目錄不是安全 sandbox；實際工具權限仍由 AGY 與 Android App 使用者權限決定。

## 語言與文件

- 分析、設計、review、驗證與結案報告預設使用繁體中文；程式碼、API、命令與錯誤原文可保留英文。
- 新文件使用 kebab-case 檔名並在文件開頭標示 `Revision` 與日期；`AGENTS.md` 保留標準檔名。
- 文件只描述已由程式、測試、官方文件或實機證據確認的行為；尚未驗證者標記 `NOT VERIFIED`。

## 安全與隱私

- 授權的 `(user_id, chat_id)` 配對檢查必須先於下載附件、建立暫存檔或啟動 AGY。
- 不新增 root、全域略過權限、任意遠端 shell、不必要的入站埠，或 Android system/network 變更。
- Token、cookie、OAuth code/verifier、private key、登入資料與真實 Bot Token 不得進入 source、文件、測試、commit、branch、PR、issue、CI log 或 Git 歷史。
- 不公開真實 Telegram ID／對話、公司或個人 email、本機使用者名稱／路徑／hostname、內網位址、裝置識別碼、私人 repository／issue、截圖或原始實機 log。
- 生產 config 必須在 checkout 外的 Termux 私人目錄，權限 0600；workspace 與其目錄權限為 0700。公開 config 只使用中性假值，測試只使用合成資料。
- 不提交或分發第三方 CLI binary。需要 AGY 時，依官方來源在目標裝置獨立安裝與核實。
- `.gitignore` 不能移除已進入 Git 歷史的秘密；push 前檢查追蹤檔案、新 commit 的完整內容與 metadata。

## 修改與驗證

- 保留既有檔案與未提交修改；有衝突時使用隔離 branch 或 worktree，不覆蓋他人工作。
- 修改前從官方文件或實際 source 核對欄位、格式、單位、限制、回傳結構與副作用。
- 採最小修改，維持 stdlib-only runtime；不為未確認需求建立多 agent framework 或無用 adapter。
- 本機測試不得執行真實 AGY、真實 Telegram Bot 或解析 PATH 啟動未知 agent。subprocess、取消及 timeout 測試必須使用合成 executable 或 mock。
- 每次功能或 bug fix 後逐行走讀修改，檢查邊界條件與副作用，再執行：

  ```sh
  python -m compileall -q .
  python -m unittest discover -s tests -v
  ```

- 依變更補充授權、busy、timeout、cancel、cleanup、UTF-16 分段、topic routing、附件路徑／大小／MIME、callback scope、錯誤路由與最小權限測試。
- exit 0 或單一 `SUCCESS` 字串不等於 AGY 成功；實機驗收必須檢查完整 JSON、非空 response、error 與 `denied_actions`。
- mock、compile、unit test 與 doctor 不得寫成 Android、Telegram 或 AGY live PASS。
- 變更完成後從真正消費端驗證；只有 UI、實體裝置、網路或時間條件才能觀察時，列出人工驗收輸入及明確通過條件。

## Android／Termux 部署

- 僅在使用者明確指定且即時核實的裝置，以非 root Termux App 使用者執行實機步驟；不得回退到本機 agent binary。
- 安裝、升級、doctor、回滾與 `termux-services` 流程以 `docs/termux-setup.md` 與 `docs/service-setup.md` 為準。
- 同一 Bot Token 同時只能有一個 poller。啟動服務前核對既有 process/service 身份；不得按名稱批次終止未知程序。
- 真實往返使用專用測試 Bot。缺裝置、官方 AGY、登入、Token 或授權配對時，完成離線工作後將相依項目標記 `NOT VERIFIED`。
- 部署或回滾後核對版本 marker、doctor、service status、`/status` 與真實 Telegram → AGY → Telegram 往返。

## Git 與 provenance

- Commit subject 使用 Conventional Commits：`<type>(<scope>): <imperative summary>`。
- Author／Committer 使用 repository owner 的 GitHub public identity 與 noreply email；不得把 agent 偽裝成人類作者。
- Agent 實質參與的 commit 在 message body 最後加入 `Assisted-by: Codex`。只有能從當次 runtime 紀錄核實 model 名稱時才附 model。
- 不擅自 merge PR 或改寫已推送歷史；依當次使用者授權決定 push、merge 與部署範圍。

## 公開權威來源

- Telegram Bot API: https://core.telegram.org/bots/api
- AGY headless: https://www.antigravity.google/docs/cli/headless/
- AGY permissions: https://www.antigravity.google/docs/permissions?tab=cli
- Termux: https://github.com/termux/termux-app
- termux-services: https://github.com/termux/termux-services

## 術語白話解釋

- sandbox：限制程式可以存取哪些檔案、程序與網路的隔離機制。
- long polling／poller：Bot 持續向 Telegram 要求新訊息的方式／執行這項工作的程序。
- mock／synthetic executable：以假的介面或可執行檔重現受控行為，不接觸真實服務。
- metadata／provenance：commit 的作者等附加資料／記錄 agent 參與來源的資訊。
- live PASS／NOT VERIFIED：真實裝置與服務已通過／尚未完成真實環境驗證。
