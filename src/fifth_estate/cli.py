"""`fifth` command-line entry point."""

from __future__ import annotations

import asyncio
import logging

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

app = typer.Typer(no_args_is_help=True, add_completion=False, help="第五權 The Fifth Estate pipeline")
console = Console()


@app.callback()
def _setup(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)],
    )
    for noisy in ("httpx", "httpcore", "hpack", "trafilatura", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _ids(outlets: str | None) -> list[str] | None:
    return [x.strip() for x in outlets.split(",") if x.strip()] if outlets else None


@app.command()
def ingest(
    outlets: str = typer.Option(None, help="Comma-separated outlet ids (default: all)"),
    since_hours: float = typer.Option(36, help="Ignore candidates published before this window"),
    max_fetch: int = typer.Option(150, help="Max article fetches per outlet per run"),
    summary: str = typer.Option(None, help="Append a Markdown summary to this file (e.g. $GITHUB_STEP_SUMMARY)"),
) -> None:
    """Discover and fetch new political articles from every outlet."""
    from .ingest.run import ingest as run_ingest

    reports = asyncio.run(run_ingest(_ids(outlets), since_hours, max_fetch))
    cols = ("outlet", "discovered", "new", "ok", "skipped", "failed", "source errors")
    rows = []
    for r in reports:
        failed = r.statuses["http_error"] + r.statuses["extract_failed"]
        skipped = sum(v for k, v in r.statuses.items() if k not in ("ok", "http_error", "extract_failed"))
        rows.append((r.outlet_id, r.discovered, r.new, r.statuses["ok"], skipped, failed, len(r.errors)))
    t = Table(title="ingest")
    for col in cols:
        t.add_column(col)
    for row in rows:
        t.add_row(*map(str, row))
    console.print(t)
    errors = [e for r in reports for e in r.errors]
    for e in errors:
        console.print(f"[yellow]source error[/] {e}")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### ingest\n\n| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n")
            for row in rows:
                f.write("| " + " | ".join(map(str, row)) + " |\n")
            if errors:
                f.write("\n**Source errors**\n\n" + "\n".join(f"- `{e}`" for e in errors) + "\n")
            f.write("\n")


def _report(name: str, d: dict, summary: str | None = None) -> None:
    console.print(f"[bold]{name}[/] " + "  ".join(f"{k}={v}" for k, v in d.items()))
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"- **{name}**: " + ", ".join(f"{k}={v}" for k, v in d.items()) + "\n")


@app.command()
def stage1(limit: int = typer.Option(None, help="Max articles (default: models.yaml limits)"), summary: str = typer.Option(None)) -> None:
    """Stage 1: in-scope filter, takeaways, topic/subtopic, news type."""
    from .annotate import stage1 as s1
    from .config import load_yaml

    limit = limit or load_yaml("models.yaml")["limits"]["stage1_per_run"]
    _report("stage1", s1.run(limit), summary)


@app.command()
def stage2(
    mode: str = typer.Option("batch", help="batch (collect + submit) | collect | submit | sync"),
    limit: int = typer.Option(None),
    summary: str = typer.Option(None),
) -> None:
    """Stage 2: tone, 綠藍 lean, 抗中友中 stance."""
    from .annotate import stage2 as s2
    from .config import load_yaml
    from .store import lake

    limit = limit or load_yaml("models.yaml")["limits"]["stage2_per_run"]
    run_id = lake.new_run_id()
    if mode in ("batch", "collect"):
        _report("stage2 collect", s2.collect(run_id), summary)
    if mode in ("batch", "submit"):
        _report("stage2 submit", s2.submit(limit, run_id), summary)
    if mode == "sync":
        _report("stage2 sync", s2.run_sync(limit, run_id)[0], summary)


@app.command()
def embed(limit: int = typer.Option(2000), summary: str = typer.Option(None)) -> None:
    """Embed in-scope articles (title + takeaways) for event clustering."""
    from .cluster import embed as em

    _report("embed", em.run(limit), summary)


@app.command()
def annotate(summary: str = typer.Option(None)) -> None:
    """Everything the hourly job does after ingest: stage1 → stage2 (collect + submit) → embed."""
    import os

    if not os.environ.get("OPENAI_API_KEY"):
        console.print("[yellow]OPENAI_API_KEY not set — skipping annotation[/]")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write("- annotation skipped: OPENAI_API_KEY not set\n")
        return
    stage1(limit=None, summary=summary)
    stage2(mode="batch", limit=None, summary=summary)
    embed(limit=2000, summary=summary)


@app.command()
def publish(
    summary: str = typer.Option(None),
    daily: bool = typer.Option(False, help="Also run the once-a-day jobs (subtopic discovery, public snapshot)"),
    skip_llm: bool = typer.Option(False, help="No LLM calls (event titles fall back to a member headline)"),
    site_dir: str = typer.Option(None, help="Output dir for dashboard JSON (default web/public/data)"),
) -> None:
    """Wire flags → events → (daily) subtopic discovery → metrics + JSON export."""
    import os
    from pathlib import Path

    from .cluster import events, subtopics
    from .export import site_data
    from .ingest import dedupe
    from .store import lake

    run_id = lake.new_run_id()
    has_key = bool(os.environ.get("OPENAI_API_KEY")) and not skip_llm
    _report("wire flags", dedupe.run(run_id=run_id), summary)
    _report("events", events.run(run_id=run_id, use_llm=has_key), summary)
    if daily and has_key:
        _report("subtopics", subtopics.run(run_id=run_id), summary)
    out = Path(site_dir) if site_dir else site_data.SITE_DATA
    _report("export", site_data.export(out_dir=out, public_dir=site_data.PUBLIC_DATA if daily else None), summary)


@app.command("gold-sample")
def gold_sample(
    name: str = typer.Option("gold"),
    n: int = typer.Option(150),
    days: int = typer.Option(14),
    weekly: bool = typer.Option(False, help="MBD weekly check: 2 per lean bucket + ≥1 per outlet"),
) -> None:
    """Draw a stratified set of articles for human labeling (saved in the data repo)."""
    from .validate import sample

    ids = sample.weekly(days=7) if weekly else sample.gold(n=n, days=days)
    path = sample.save(name, ids)
    console.print(f"{len(ids)} articles → {path}")


@app.command()
def label(set_name: str = typer.Option("gold", "--set"), coder: str = typer.Option(..., help="Your name/initials"), port: int = 8765) -> None:
    """Open the local labeling page (source-blinded, no model scores)."""
    from .validate import label_app

    label_app.serve(set_name, coder, port)


@app.command("eval")
def evaluate_models(
    set_name: str = typer.Option("gold", "--set"),
    coder: str = typer.Option(...),
    models: str = typer.Option("gpt-6-luna:medium,gpt-6.1-sol:low,gpt-6-astra:low"),
    coder2: str = typer.Option(None, help="Second human coder for human–human agreement"),
    skip_predict: bool = typer.Option(False),
) -> None:
    """Bake-off: run each model on the labeled set, then report agreement with the human labels."""
    from .validate import evaluate

    specs = [m.strip() for m in models.split(",") if m.strip()]
    if not skip_predict:
        for s in specs:
            _report("predict", evaluate.predict(set_name, s))
    console.print(evaluate.report(set_name, coder, specs, coder2))


@app.command("weekly-report")
def weekly_report(out: str = typer.Option(..., help="Markdown file to write")) -> None:
    """Scraper health, LLM spend, subtopic changes and the week's validation sample (for the weekly Issue)."""
    from pathlib import Path

    from .config import data_dir
    from .store import lake
    from .validate import sample

    week = lake.taipei_today().strftime("%G-W%V")
    ids = sample.weekly(days=7)
    sample.save(f"weekly-{week}", ids)
    health = lake.rows(
        f"""SELECT outlet_id, count(*) FILTER (WHERE status = 'ok') AS ok,
                   count(*) FILTER (WHERE status IN ('http_error', 'extract_failed')) AS failed,
                   max(created_at) FILTER (WHERE status = 'ok') AS last_ok
            FROM {lake.source('fetches', 7)} GROUP BY outlet_id ORDER BY outlet_id"""
    )
    spend = lake.rows(f"SELECT task, model, round(sum(cost_usd), 3) AS usd, sum(n_requests) AS req FROM {lake.source('llm_usage', 7)} GROUP BY ALL ORDER BY usd DESC")
    changelog = data_dir() / "registry" / "changelog.md"
    lines = [f"## 第五權週報 {week}", "", "### 抓取健康度（近 7 天）", "", "| 媒體 | 成功 | 失敗 | 最後成功 |", "|---|---|---|---|"]
    from .config import TAIPEI

    lines += [f"| {h['outlet_id']} | {h['ok']} | {h['failed']} | {h['last_ok'].astimezone(TAIPEI):%m-%d %H:%M} |" if h["last_ok"]
              else f"| {h['outlet_id']} | {h['ok']} | {h['failed']} | **24 小時以上沒有成功抓取** |" for h in health]
    lines += ["", "### LLM 花費（近 7 天）", "", "| 任務 | 模型 | 美元 | 請求數 |", "|---|---|---|---|"]
    lines += [f"| {s['task']} | {s['model']} | {s['usd']} | {s['req']} |" for s in spend]
    lines += ["", "### 子議題變動（自動新增，請檢查名稱是否中性、是否該合併）", ""]
    lines += [ln for ln in (changelog.read_text(encoding="utf-8").splitlines()[-30:] if changelog.exists() else ["（無）"])]
    lines += ["", f"### 本週驗證抽樣（{len(ids)} 篇，MBD 作法：每個五級分組 2 篇、每家媒體至少 1 篇）", "",
              "在筆電上：`cd ~/fifth-estate-data && git pull`，然後",
              f"`cd ~/fifth-estate && uv run fifth label --set weekly-{week} --coder <你的名字>`，標完後 `cd ~/fifth-estate-data && git add validation && git commit -m labels && git push`。",
              f"之後可以跑 `uv run fifth eval --set weekly-{week} --coder <你的名字> --skip-predict --models \"\"` 看一致性。"]
    Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"weekly report → {out}")


@app.command()
def stats(days: int = typer.Option(3)) -> None:
    """Article counts per outlet and day."""
    from .store import lake

    rs = lake.rows(
        f"""SELECT outlet_id, CAST(date AS VARCHAR) AS day, count(*) AS n, round(avg(n_chars)) AS chars
            FROM {lake.source('articles', days)} GROUP BY ALL ORDER BY day DESC, outlet_id"""
    )
    t = Table(title=f"articles (last {days} days)")
    for col in ("day", "outlet", "articles", "avg chars"):
        t.add_column(col)
    for r in rs:
        t.add_row(r["day"], r["outlet_id"], str(r["n"]), str(r["chars"]))
    console.print(t)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
