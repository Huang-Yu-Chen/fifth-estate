# 第五權 The Fifth Estate — notes for coding agents

Taiwanese political-news polarization tracker modeled on UPenn's Media Bias Detector (MBD).
Code: this public repo. Data (full text + labels): private repo `Huang-Yu-Chen/fifth-estate-data`
(checked out next to this repo as `../fifth-estate-data`, or set `FIFTH_DATA_DIR`). Site: https://the-fifth-estate.org
(GitHub Pages with a custom domain set in the repo's Pages settings, no CNAME file; DNS at GoDaddy, www → apex).

Scheduling: Google Cloud Scheduler jobs `fifth-estate-ingest` (hourly :07) and `fifth-estate-publish`
(every 3 h :27) in project `project-ec3ab1fc-4d15-40e8-a92`, region asia-east1, call workflow_dispatch with a
fine-grained token (expires ~2027-10; re-run `scripts/setup_cloud_scheduler.sh`). GitHub cron is backup only.

## Commands
- `uv run fifth ingest|annotate|stage1|stage2|embed|publish|stats|status` — pipeline steps (see `src/fifth_estate/cli.py`)
- `uv run fifth gold-sample | label --coder X | eval --coder X --models a:low,b:low` — validation
- `uv run pytest` — tests; `cd web && npm run build` — dashboard (Vite + React + ECharts, zh-TW)
- Dev without an API key: `FIFTH_DATA_DIR=../fifth-estate-data-local uv run python scripts/dev_fake_annotations.py`
  then `uv run fifth publish --skip-llm` (writes `web/public/data`, marked fake; never deploy it)

## Layout
- `config/outlets.yaml` sources and section rules per outlet; `config/models.yaml` model per task (user-chosen,
  don't change without asking); `config/pipeline.yaml` thresholds; `config/taxonomy.yaml` fixed topics;
  `config/subtopics_seed.yaml` seeds the living registry (`registry/subtopics.yaml` in the data repo).
- `prompts/*.md` codebooks. Changing a prompt changes its hash (recorded per label); bump `CLEAN_VERSION`
  in `ingest/clean.py` when cleaning rules change.
- `web/`: clean URLs (BrowserRouter) served from the domain root. GitHub Pages has no rewrites, so the build writes
  one HTML file per route with its own title/description/canonical (`/events` → `events.html`), plus `404.html`
  and `robots.txt` (`web/vite.config.ts`; page copy in `web/src/site.ts`). Old `#/…` links are converted in `main.tsx`.
- Lake: append-only zstd Parquet `{table}/date=YYYY-MM-DD/*.parquet`, DuckDB queries; superseded rows are
  resolved with `lake.latest()` (newest `created_at` per key). Schemas in `store/schemas.py`.
- OpenAI quota used up (`llm.client.QUOTA_CODES`) → `annotate` pauses instead of failing (`annotate/pause.py`,
  state in the data repo's `state/pause.json`): the crawl continues, paused runs only probe with a one-word
  embedding, publish updates wire flags but not the site. When the quota is back the backlog is labeled
  newest first with the look-back reaching the outage start, and the site resumes once nothing is left.
  A quota failure is never recorded as an attempt. `fifth status` shows the state; `publish --force` overrides.

## Decisions (from the user, 2026-10-13)
- Crawl politics/兩岸/國際 sections only (no homepage scraping); 中時/民視 via Yahoo provider archives
  (Cloudflare blocks them — never try to bypass bot protection).
- Lean axis uses MBD's definition (support = viewpoints, politicians, policies); TPP projected by viewpoint
  alignment; no per-party favorability. New 抗中↔友中 axis, null when no China angle.
- Models: Stage 1 & Stage 2 `gpt-6-luna`, embeddings `text-embedding-3-large` (1024 d), synthesis
  `gpt-6.1-sol`. Week-2 bake-off (Luna vs 6.1-Sol vs Astra on a ~150-article human gold set) decides Stage 2.
- Polarization P = mean over axes of 100·(1/K²)ΣΣ|x̃o−x̃p| (equal outlet weights, EB shrinkage κ=5).
- Site stays `status: beta` (+ `noindex`, which the build adds while beta) until the gold-set validation is done.
- Never publish full article text; site shows titles, links, scores, short model reasons.
