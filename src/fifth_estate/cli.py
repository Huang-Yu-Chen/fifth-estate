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
