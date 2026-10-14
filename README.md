# 第五權 The Fifth Estate

台灣網路新聞的政治極化偵測器。以賓州大學 [Media Bias Detector](https://mediabiasdetector.seas.upenn.edu/methodology/) 為模版，追蹤各家媒體的政治、兩岸與涉台外交新聞，計算：

- 每個議題 × 每家媒體：**報導數**、**親綠 ↔ 親藍**、**抗中 ↔ 友中**
- 每個議題：單一的 **極化指數**（0–100）

網站：<https://the-fifth-estate.org>

## 運作方式

```
GitHub Actions（本 repo）
 ├ ingest.yml   每小時   發現新文章 → 抓取 → 抽取清理 → Stage 1 分類 → Stage 2 立場評分 → 嵌入
 ├ publish.yml  每 3 小時 事件分群 → 子議題整併 → 指標與極化指數 → 儀表板（GitHub Pages，自訂網域）
 └ weekly.yml   每週     驗證抽樣、子議題審核、資料健康報告
私有 repo fifth-estate-data：新聞全文與標註（Parquet，DuckDB 查詢）
```

排程由 Google Cloud Scheduler（台灣機房）呼叫 GitHub 的 workflow_dispatch 觸發；GitHub 自己的 cron 只當備援，
因為它在這個 repo 幾乎每次都被略過。設定方式見 `scripts/setup_cloud_scheduler.sh`（需要一個只限本 repo、
只有 Actions 讀寫權限的 GitHub token，一年到期後重跑一次即可）。

新聞全文只存在私有 repo；公開網站只放標題、連結、分數與簡短理由。

## 本機開發

```bash
uv sync
cp .env.example .env          # 填入 OPENAI_API_KEY
uv run fifth ingest --outlets ltn,cna --since-hours 6
uv run fifth stats
uv run pytest
```

媒體來源與版面規則在 `config/outlets.yaml`；樣板文字字典在 `config/boilerplate.yaml`。
