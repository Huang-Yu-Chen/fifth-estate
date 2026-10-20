"""`fifth labeling …`: run the blinded labeling site for human coders (see labeling/__init__.py).

    fifth gold-sample --name gold                 # draw the set (existing command)
    fifth labeling publish --set gold --coders 2  # put it on the site, blinded
    fifth labeling invite --id lin --name 林小華   # personal link for each labeler
    fifth labeling status                         # progress
    fifth labeling sync                           # labels → data repo, then `fifth eval --coder all`
"""

from __future__ import annotations

import os
import re
import secrets
import statistics
from datetime import datetime

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .. import config  # noqa: F401  (loads .env, where deploy_labeling.sh put FIFTH_LABEL_STORE and FIFTH_LABEL_URL)

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Blinded labeling site for human coders (validates the AI labels).")
console = Console()

STORE_HELP = "gs://bucket or a local folder (default: $FIFTH_LABEL_STORE)"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _store(url: str | None):
    from .store import open_store

    return open_store(url)


def _set_labeler(doc: dict, labeler_id: str, name: str | None, token: str, sets: list[str] | None, reissue: bool) -> dict:
    from .server import token_hash, utc_now

    cur = next((x for x in doc["labelers"] if x["id"] == labeler_id), None)
    if cur and not reissue:
        raise typer.BadParameter(f"{labeler_id} already exists; add --reissue to give them a new link", param_hint="--id")
    if not cur:
        cur = {"id": labeler_id, "created_at": utc_now(), "sets": None}
        doc["labelers"].append(cur)
    cur.update(name=name or cur.get("name") or labeler_id, token_sha256=token_hash(token), active=True, issued_at=utc_now())
    if sets is not None:
        cur["sets"] = sets
    return doc


def _edit_labelers(s, change) -> dict:
    from .store import update

    return update(s, "labelers.json", change, {"labelers": []})[0]


@app.command()
def publish(
    set_name: str = typer.Option(..., "--set", help="A set in validation/sets/ (e.g. from `fifth gold-sample`)"),
    coders: int = typer.Option(2, help="Independent labelers per article (0 = every labeler labels every article)"),
    title: str = typer.Option(None, help="Name labelers see (default: the set name)"),
    only_new: bool = typer.Option(False, "--only-new", help="Leave the set as it is if it is already on the site, even closed (scheduled runs)"),
    store: str = typer.Option(None, help=STORE_HELP),
) -> None:
    """Put a label set on the site, blinded. Re-run after a codebook change: keys and labels are kept."""
    from . import bundle
    from .store import load, save

    s = _store(store)
    prev = load(s, f"sets/{set_name}.json")
    if prev and only_new:
        console.print(f"{set_name} is already on the site; left as it is")
        return
    b, missing = bundle.build(set_name, coders, prev, title)
    save(s, f"sets/{set_name}.json", b, compact=True)
    bundle.mark_set(set_name, coders)
    per = f"{coders} labeler{'s' if coders > 1 else ''} per article" if coders else "every labeler labels every article"
    console.print(f"{set_name}: {len(b['items'])} articles on the site ({per}) → {s.url}")
    if missing:
        console.print(f"[yellow]{len(missing)} articles of the set are not in the local lake — pull the data repo and publish again[/]")


@app.command()
def close(set_name: str = typer.Option(..., "--set"), store: str = typer.Option(None, help=STORE_HELP)) -> None:
    """Take a set off the site. Its labels stay in the bucket and can still be synced."""
    from .store import update

    def change(b: dict | None) -> dict:
        if b is None:
            raise typer.BadParameter(f"{set_name} is not on the site", param_hint="--set")
        return {**b, "active": False}

    update(_store(store), f"sets/{set_name}.json", change, None, compact=True)
    console.print(f"{set_name} closed")


@app.command()
def invite(
    labeler_id: str = typer.Option(..., "--id", help="Short ASCII id; becomes the coder name in validation/labels/{set}-{id}.jsonl"),
    name: str = typer.Option(None, help="Name shown on the site (default: the id)"),
    sets: str = typer.Option(None, help="Comma-separated sets this labeler may label (default: every open set)"),
    reissue: bool = typer.Option(False, help="New link for an existing labeler; the old one stops working"),
    url: str = typer.Option(None, help="Site address (default: $FIFTH_LABEL_URL)"),
    store: str = typer.Option(None, help=STORE_HELP),
) -> None:
    """Add a labeler (or reissue their link) and print their personal link. It is shown only once."""
    if not ID_RE.match(labeler_id):
        raise typer.BadParameter("lowercase letters, digits, - and _ (at most 32)", param_hint="--id")
    url = url or os.environ.get("FIFTH_LABEL_URL")
    if not url:
        raise typer.BadParameter("pass --url or set FIFTH_LABEL_URL", param_hint="--url")
    token = secrets.token_urlsafe(24)
    allowed = [x.strip() for x in sets.split(",") if x.strip()] if sets else None
    _edit_labelers(_store(store), lambda doc: _set_labeler(doc, labeler_id, name, token, allowed, reissue))
    console.print(f"Personal link for {escape(name or labeler_id)}. Send it privately: anyone with it can label as them.")
    print(f"{url.rstrip('/')}/#join={token}")


@app.command()
def revoke(labeler_id: str = typer.Option(..., "--id"), store: str = typer.Option(None, help=STORE_HELP)) -> None:
    """Stop a labeler's link from working (within 30 seconds). Their labels are kept."""

    def change(doc: dict) -> dict:
        cur = next((x for x in doc["labelers"] if x["id"] == labeler_id), None)
        if not cur:
            raise typer.BadParameter(f"no labeler {labeler_id}", param_hint="--id")
        cur["active"] = False
        return doc

    _edit_labelers(_store(store), change)
    console.print(f"{labeler_id} revoked")


@app.command()
def status(store: str = typer.Option(None, help=STORE_HELP)) -> None:
    """Progress per set and labeler, straight from the bucket."""
    from ..config import TAIPEI
    from . import assign
    from .store import load

    s = _store(store)
    for name in s.list("sets/"):
        b = load(s, name)
        keys = [it["key"] for it in b["items"]]
        k = b["coders_per_article"]
        labels = {f.rsplit("/", 1)[-1].removesuffix(".json"): (load(s, f) or {}).get("labels", {}) for f in s.list(f"labels/{b['set']}/")}
        counts = assign.done_counts(labels)
        full = sum(1 for key in keys if counts[key] >= max(k, 1))
        title = f"{b['set']} ({b.get('title')}){'' if b.get('active', True) else ' — closed'}: {len(keys)} articles, "
        title += f"{k} per article, {full} complete, {assign.needed(keys, k, labels)} labels to go" if k else f"everyone labels all, {full} labeled at least once"
        t = Table(title=title, title_justify="left")
        for col in ("labeler", "labeled", "skipped", "median seconds", "last labeled (Taipei)"):
            t.add_column(col)
        for lab_id, mine in sorted(labels.items()):
            done = [v for v in mine.values() if not v.get("skipped")]
            secs = [v["seconds"] for v in done if v.get("seconds")]
            last = max((v["labeled_at"] for v in mine.values()), default=None)
            t.add_row(lab_id, str(len(done)), str(len(mine) - len(done)), str(round(statistics.median(secs))) if secs else "–",
                      datetime.fromisoformat(last).astimezone(TAIPEI).strftime("%m-%d %H:%M") if last else "–")
        console.print(t)
    people = load(s, "labelers.json", {"labelers": []})["labelers"]
    console.print("labelers: " + (", ".join(f"{p['id']} ({escape(p['name'])}){'' if p.get('active', True) else ' revoked'}" for p in people) or "none yet"))


@app.command()
def sync(store: str = typer.Option(None, help=STORE_HELP)) -> None:
    """Copy the site's labels into the data repo (validation/labels/{set}-{coder}.jsonl) for `fifth eval`.
    GitHub Actions does this after every publish run and commits it (labeling.yml); to get the labels sooner,
    `gh workflow run labeling.yml` and pull the data repo, rather than syncing here."""
    from ..config import data_dir
    from .sync import run

    summary = run(_store(store))
    for set_name, d in summary.items():
        console.print(f"{set_name}: {d['labels']} labels in the bucket, {d['appended']} new lines, coders {', '.join(d['coders'])}")
    if not summary:
        console.print("no labels yet")
    console.print("Commit them in the data repo right away (GitHub Actions commits the same files):")
    print(f"cd {data_dir()} && git add validation state/labeling_sync.json && git commit -m 'labels from the labeling site' && git pull --rebase && git push")


@app.command()
def serve(
    store: str = typer.Option(None, help="A local folder (default: $FIFTH_LABEL_STORE)"),
    port: int = typer.Option(8767),
    dev: bool = typer.Option(False, help="Add (or refresh) a test labeler 'dev' and print its link"),
) -> None:
    """Run the site on this computer, for development. Production runs on Cloud Run (scripts/deploy_labeling.sh)."""
    import uvicorn

    from .server import create_app

    s = _store(store)
    if dev:
        if s.url.startswith("gs://"):
            raise typer.BadParameter("--dev only works with a local folder, not the production bucket", param_hint="--store")
        token = secrets.token_urlsafe(24)
        _edit_labelers(s, lambda doc: _set_labeler(doc, "dev", "測試標註者", token, None, reissue=True))
        console.print("Test link:")
        print(f"http://127.0.0.1:{port}/#join={token}", flush=True)
    uvicorn.run(create_app(s), host="127.0.0.1", port=port, log_level="warning")
