"""Copy the labeling site's labels into the data repo, in the format `fifth label` writes
(validation/labels/{set}-{coder}.jsonl, last line per article wins), so `fifth eval` and the
monitoring dashboard read them like any other labels. Append-only and idempotent: a label revision
already in the file is not written again.

state/labeling_sync.json records when this last ran ("synced_at") and, per set, how many labels the
bucket held, how many lines were appended and who the coders are.
"""

from __future__ import annotations

import json

from ..config import data_dir
from ..store import lake
from ..validate.label_app import labels_path
from .store import Store, load

FIELDS = ("scope", "lean", "china_relevance", "china", "tone", "news_type")


def record(set_name: str, coder: str, article_id: str, lab: dict) -> dict:
    return {
        "article_id": article_id,
        **{f: lab.get(f) for f in FIELDS},
        "note": lab.get("note", ""),
        "skipped": bool(lab.get("skipped")),
        "skip_reason": lab.get("skip_reason", ""),
        "coder": coder,
        "labeled_at": lab["labeled_at"],
        "set": set_name,
        "source": "site",
        "seconds": lab.get("seconds"),
        "codebook": lab.get("codebook"),
        "rev": lab.get("rev", 1),
    }


def _seen(path) -> set[tuple]:
    if not path.exists():
        return set()
    lines = (json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
    return {(d.get("article_id"), d.get("labeled_at"), d.get("rev")) for d in lines}


def run(store: Store) -> dict:
    summary = {}
    for name in store.list("sets/"):
        bundle = load(store, name)
        set_name = bundle["set"]
        ids = {it["key"]: it["article_id"] for it in bundle["items"]}
        files = store.list(f"labels/{set_name}/")
        if not files:
            continue
        total = appended = 0
        coders = []
        for fname in files:
            doc = load(store, fname) or {}
            coder = doc.get("labeler") or fname.rsplit("/", 1)[-1].removesuffix(".json")
            coders.append(coder)
            path = labels_path(set_name, coder)
            seen = _seen(path)
            new = []
            for key, lab in sorted(doc.get("labels", {}).items(), key=lambda kv: kv[1]["labeled_at"]):
                total += 1
                if key in ids and (ids[key], lab["labeled_at"], lab.get("rev", 1)) not in seen:
                    new.append(record(set_name, coder, ids[key], lab))
            if new:
                with path.open("a", encoding="utf-8") as f:
                    f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in new)
            appended += len(new)
        summary[set_name] = {"labels": total, "appended": appended, "coders": sorted(coders)}
    out = data_dir() / "state" / "labeling_sync.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"synced_at": lake.now_utc().isoformat(timespec="seconds"), "sets": summary},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
