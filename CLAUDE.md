# 第五權 The Fifth Estate — notes for coding agents

Taiwanese political-news polarization tracker modeled on UPenn's Media Bias Detector (MBD).
Code: this public repo. Data (full text + labels): private repo `Huang-Yu-Chen/fifth-estate-data`
(checked out next to this repo as `../fifth-estate-data`, or set `FIFTH_DATA_DIR`). Site: https://the-fifth-estate.org
(Firebase Hosting site `the-fifth-estate` in the same Google Cloud project, also https://the-fifth-estate.web.app;
DNS at GoDaddy, www → apex): a static dashboard deployed only when `web/` changes (`site.yml`) that reads the public
API `fifth-estate-api` (Cloud Run, asia-east1, `src/fifth_estate/api`, deployed by `api.yml`), which also renders the
pages that depend on the data; data updates never redeploy anything. (Until the domain's DNS is moved to Firebase,
see the end of `scripts/setup_backend.sh`, it still points at the last GitHub Pages deploy of 2026-10-20.)

Scheduling: Google Cloud Scheduler jobs `fifth-estate-ingest` (hourly :07) and `fifth-estate-publish`
(every 3 h :27) in project `project-ec3ab1fc-4d15-40e8-a92`, region asia-east1, call workflow_dispatch with a
fine-grained token (expires ~2027-10; re-run `scripts/setup_cloud_scheduler.sh`). GitHub cron is backup only.

## Commands
- `uv run fifth ingest|annotate|stage1|stage2|embed|publish|stats|status` — pipeline steps (see `src/fifth_estate/cli.py`)
- `uv run fifth gold-sample | label --coder X | eval --coder X --models a:low,b:low` — validation
- `uv run fifth labeling publish --set gold | invite --id X --name Y | revoke | close | status | sync` — the
  blinded labeling site for invited human coders; `fifth eval --coder all` pools every coder of a set.
  Locally: `fifth labeling serve --dev --store <folder>` (folder holds a published set; prints a login link)
- `uv run pytest` — tests; `cd web && npm run build` — dashboard (Vite + React + ECharts, zh-TW)
- `uv run fifth export` (snapshot from the lake as is) + `uv run fifth serve` (API on :8000) + `cd web && npm run dev`
  (proxies `/v1` to it) — the site locally on real or fake data; after `npm run build`, :8000/unit/… shows the
  HTML the API renders for unit pages
- Dev without an API key: `FIFTH_DATA_DIR=../fifth-estate-data-local uv run python scripts/dev_fake_annotations.py`
  then `uv run fifth publish --skip-llm` (writes `build/serving/serving.sqlite`, marked fake; never upload it)

## Layout
- `config/outlets.yaml` sources and section rules per outlet; `config/models.yaml` model per task (user-chosen,
  don't change without asking); `config/pipeline.yaml` thresholds; `config/taxonomy.yaml` fixed topics;
  `config/subtopics_seed.yaml` seeds the living registry (`registry/subtopics.yaml` in the data repo).
- `prompts/*.md` codebooks. Changing a prompt changes its hash (recorded per label); bump `CLEAN_VERSION`
  in `ingest/clean.py` when cleaning rules change.
- `web/`: clean URLs (BrowserRouter) served from the domain root by Firebase Hosting (`firebase.json`). The build
  writes the fixed pages with their own title/description/canonical (`/events` → `events.html`), `404.html` and
  `page-templates.json` (`web/vite.config.ts`; page copy in `web/src/site.ts`). Firebase sends `/unit/…`,
  `/outlet/…`, `sitemap.xml` and `robots.txt` to the API, which renders them from those templates and the current
  snapshot (`api/pages.py`): a new event gets 200 and its own link preview with no deploy, an unknown unit 404.
  `site.yml` keeps the previous build's `assets/` through one more deploy. Old `#/…` links are converted in `main.tsx`.
- Lake: append-only zstd Parquet `{table}/date=YYYY-MM-DD/*.parquet`, DuckDB queries; superseded rows are
  resolved with `lake.latest()` (newest `created_at` per key). Schemas in `store/schemas.py`.
- OpenAI quota used up (`llm.client.QUOTA_CODES`) → `annotate` pauses instead of failing (`annotate/pause.py`,
  state in the data repo's `state/pause.json`): the crawl continues, paused runs only probe with a one-word
  embedding, publish updates wire flags but not the site. When the quota is back the backlog is labeled
  newest first with the look-back reaching the outage start, and the site resumes once nothing is left.
  A quota failure is never recorded as an attempt. `fifth status` shows the state; `publish --force` overrides.
- Serving: publish writes one read-only SQLite snapshot (`export/serving.py`; layout in `api/schema.py`: gzipped
  JSON docs + one row per article) and uploads `serving.sqlite.gz` over the versioned object
  `gs://project-ec3ab1fc-4d15-40e8-a92-serving/serving.sqlite.gz`; every API instance switches to the new
  generation within a minute (`api/snapshots.py`). Roll back by copying an older generation over it
  (`gcloud storage ls -a gs://…/serving.sqlite.gz`, then `gcloud storage cp gs://…/serving.sqlite.gz#GEN gs://…/serving.sqlite.gz`).
  The API rereads the deployed site's `page-templates.json` (`FIFTH_SITE_TEMPLATES`) the same way.
  GitHub Actions sign in by Workload Identity Federation (no keys); all of it, Firebase included, is in
  `scripts/setup_backend.sh`.
  Anonymous page views: the site beacons `POST /v1/hit`, logged as one JSON line per view (`api/hits.py`).
- Labeling site (`src/fifth_estate/labeling`, `scripts/deploy_labeling.sh`): Cloud Run `fifth-estate-labeling`
  (max 1 instance) + private bucket `<project>-labeling`, deliberately separate from the public API — it serves
  blinded full text, so nothing from that bucket may go through `fifth-estate-api`. Invite-only (personal links
  `…/#join=<token>`, only token hashes stored), `noindex`. Labelers see exactly what Stage 2 sees
  (`annotate.common.blinded`) and the model's codebook cut from `prompts/` (`labeling/codebook.py`); never the
  outlet, URL, date, model output, other labelers' labels or article ids (the browser gets random keys).
  `assign.py` gives each article to `coders_per_article` independent labelers. `fifth labeling sync` appends
  labels to `validation/labels/{set}-{coder}.jsonl` (same format as `fifth label`) and writes
  `state/labeling_sync.json`; the private monitoring dashboard reads both. Sync is automatic: `labeling.yml`
  runs it after every publish run (daily cron backup) and commits both to the data repo; `gh workflow run
  labeling.yml` syncs now. `weekly.yml` puts the week's sample on the site (`labeling publish --only-new`; a
  re-run of `weekly-report` keeps the week's sample). Both sign in as `fifth-estate-labeling-sync` (Workload
  Identity Federation, main only), which can read the labeling bucket and write only `sets/weekly-*` (an IAM
  condition) — never as the public API's accounts. The bucket is the repository variable `FIFTH_LABEL_STORE`;
  the grants are in `scripts/deploy_labeling.sh`. This repo's Actions logs are public: print counts, not coder names.

## Decisions (from the user, 2026-10-13 unless dated)
- Crawl politics/兩岸/國際 sections only (no homepage scraping); 中時/民視 via Yahoo provider archives
  (Cloudflare blocks them — never try to bypass bot protection).
- Lean axis uses MBD's definition (support = viewpoints, politicians, policies); TPP projected by viewpoint
  alignment; no per-party favorability. New 抗中↔友中 axis, null when no China angle.
- Models: Stage 1 & Stage 2 `gpt-6-luna`, embeddings `text-embedding-3-large` (1024 d), synthesis
  `gpt-6.1-sol`. Week-2 bake-off (Luna vs 6.1-Sol vs Astra on a ~150-article human gold set) decides Stage 2.
- Polarization P = mean over axes of 100·(1/K²)ΣΣ|x̃o−x̃p| (equal outlet weights, EB shrinkage κ=5).
- Site stays `status: beta` (+ `noindex`, which the build and the API's pages add while beta) until the gold-set
  validation is done.
- Never publish full article text; site shows titles, links, scores, short model reasons.
- (2026-10-19) Human validation runs on the invite-only, blinded labeling site (Cloud Run + private bucket), each
  article labeled by 2 independent coders, same fields as the `fifth label` form.
- (2026-10-20) The weekly samples go on the labeling site too, 1 coder per article. GitHub Actions reach the
  labeling bucket only as their own account `fifth-estate-labeling-sync` (read all, write weekly sets only).
- (2026-10-20) Pages that depend on the data are rendered by Cloud Run behind Firebase Hosting, rather than a
  daily site rebuild or Cloud Run domain mapping (Preview); Cloud Run stays at min-instances 0 (a cold start of
  about 4 s on those pages is accepted; min-instances 1 would cost about US$10–13 a month).
