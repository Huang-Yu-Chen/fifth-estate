# 第五權 The Fifth Estate

台灣網路新聞的政治極化偵測器。以賓州大學 [Media Bias Detector](https://mediabiasdetector.seas.upenn.edu/methodology/) 為模版，追蹤各家媒體的政治、兩岸與涉台外交新聞，計算：

- 每個議題 × 每家媒體：**報導數**、**親綠 ↔ 親藍**、**抗中 ↔ 友中**
- 每個議題：單一的 **極化指數**（0–100）

網站：<https://the-fifth-estate.org>

## 運作方式

```
GitHub Actions（本 repo）
 ├ ingest.yml   每小時   發現新文章 → 抓取 → 抽取清理 → Stage 1 分類 → Stage 2 立場評分 → 嵌入
 ├ publish.yml  每 3 小時 事件分群 → 子議題整併 → 指標與極化指數 → 資料庫快照 → Cloud Storage
 ├ api.yml      API 程式改動時   部署後端 API（Cloud Run，台灣機房）
 ├ site.yml     網頁程式改動時   部署網站（GitHub Pages，自訂網域）
 └ weekly.yml   每週     驗證抽樣、子議題審核、資料健康報告
私有 repo fifth-estate-data：新聞全文與標註（Parquet，DuckDB 查詢）
```

網站（the-fifth-estate.org）只是前端程式，資料向後端 API 讀取；publish 每次產生一份唯讀的 SQLite 資料庫快照
上傳到 Cloud Storage，API 在一分鐘內自動換上，資料更新不必重新部署網站。API 文件見
<https://fifth-estate-api-461716033038.asia-east1.run.app/docs>。Google Cloud 上的設定（儲存桶、服務帳號、
GitHub Actions 免金鑰登入）都在 `scripts/setup_backend.sh`，可重跑。

排程由 Google Cloud Scheduler（台灣機房）呼叫 GitHub 的 workflow_dispatch 觸發；GitHub 自己的 cron 只當備援，
因為它在這個 repo 幾乎每次都被略過。設定方式見 `scripts/setup_cloud_scheduler.sh`（需要一個只限本 repo、
只有 Actions 讀寫權限的 GitHub token，一年到期後重跑一次即可）。

**OpenAI 額度用完時**：抓新聞不需要 OpenAI，照常每小時進行；AI 標註暫停（之後每小時只送一個極小的請求檢查額度
是否恢復），網站停在當時的資料，頁首顯示「資料暫停更新」。額度恢復後自動補標暫停期間收集的新聞（由新到舊），
全部補完才恢復更新網站，不會出現只標了一半的日子。`uv run fifth status` 可查看目前狀態。

新聞全文只存在私有 repo；公開網站只放標題、連結、分數與簡短理由。

## 本機開發

```bash
uv sync
cp .env.example .env          # 填入 OPENAI_API_KEY
uv run fifth ingest --outlets ltn,cna --since-hours 6
uv run fifth stats
uv run pytest
uv run fifth export && uv run fifth serve   # 用目前的資料建快照並在本機啟動 API（:8000）
cd web && npm run dev                       # 網站開發伺服器，/v1 會轉給本機 API
```

媒體來源與版面規則在 `config/outlets.yaml`；樣板文字字典在 `config/boilerplate.yaml`。
