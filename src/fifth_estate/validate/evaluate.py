"""Model bake-off and agreement report against human labels (MBD reports model–human vs human–human).

    fifth eval --set gold --coder yuchen --models gpt-6-luna:medium,gpt-6.1-sol:low,gpt-6-astra:low
    fifth eval --set gold --coder all ...   # every coder of the set (the labeling site gives each article to 2)
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats as st

from ..annotate import stage2
from ..annotate.common import article_text
from ..config import data_dir
from ..llm import client as llm
from ..llm.schemas import Stage2
from ..store import lake
from . import sample
from .label_app import load_labels

AXES = (("lean", "lean"), ("china", "china_stance"), ("tone", "tone"))


def _pred_path(set_name: str, spec: str) -> Path:
    p = data_dir() / "validation" / "predictions" / f"{set_name}-{spec.replace(':', '-')}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _parse_spec(spec: str) -> llm.TaskConfig:
    model, _, effort = spec.partition(":")
    base = llm.task_config("stage2")
    return llm.TaskConfig(name="stage2", model=model, reasoning_effort=effort or base.reasoning_effort,
                          service_tier="flex", max_output_tokens=base.max_output_tokens)


def predict(set_name: str, spec: str, concurrency: int = 6) -> dict:
    cfg = _parse_spec(spec)
    _, system, _ = stage2.setup(cfg)
    path = _pred_path(set_name, spec)
    done = {json.loads(ln)["article_id"] for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()} if path.exists() else set()
    ids = [i for i in sample.load(set_name) if i not in done]
    arts = [r for r in lake.rows(f"""SELECT article_id, outlet_id, title, body FROM {lake.source('articles')}
                                     QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at) = 1""") if r["article_id"] in set(ids)]

    async def go():
        client = llm.async_client()
        sem = asyncio.Semaphore(concurrency)

        async def one(a):
            async with sem:
                res = await llm.call_async(client, llm.build_body(cfg, system, article_text(a), Stage2), Stage2, "flex")
                return a["article_id"], res

        out = await asyncio.gather(*(one(a) for a in arts))
        await client.close()
        return out

    results = asyncio.run(go()) if arts else []
    cost = 0.0
    with path.open("a", encoding="utf-8") as f:
        for aid, res in results:
            c = llm.cost_usd(cfg.model, res.usage, res.service_tier)
            cost += c
            f.write(json.dumps({"article_id": aid, "status": res.status, "cost_usd": c, **(res.parsed.model_dump() if res.parsed else {})}, ensure_ascii=False) + "\n")
    llm.record_usage("eval", cfg.model, "flex", [r for _, r in results], lake.new_run_id())
    return {"model": spec, "predicted": len(results), "cost_usd": round(cost, 4)}


def _load_preds(set_name: str, spec: str) -> dict[str, dict]:
    path = _pred_path(set_name, spec)
    if not path.exists():
        return {}
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return {r["article_id"]: r for r in rows if r.get("status") == "ok"}


def _production(ids: list[str]) -> dict[str, dict]:
    src = lake.latest("stage2", where="status = 'ok'")
    rows = lake.rows(f"SELECT * FROM {src}")
    return {r["article_id"]: r for r in rows if r["article_id"] in set(ids)}


def _num(x) -> float | None:
    try:
        return None if x in (None, "", "None") else float(x)
    except (TypeError, ValueError):
        return None


def alpha_interval(a: np.ndarray, b: np.ndarray) -> float:
    """Krippendorff's alpha (interval metric) for two coders with no missing values."""
    v = np.concatenate([a, b])
    n = len(v)
    if n < 4 or np.var(v) == 0:
        return float("nan")
    d_o = (n - 1) * np.sum(2 * (a - b) ** 2)
    d_e = np.sum((v[:, None] - v[None, :]) ** 2)
    return float(1 - d_o / d_e)


def alpha_units(units: list[list[float]]) -> float:
    """Krippendorff's alpha (interval metric) for any number of coders with missing labels; `units`
    holds the values given to each article (articles with fewer than two are ignored)."""
    us = [np.asarray(u, dtype=float) for u in units if len(u) >= 2]
    v = np.concatenate(us) if us else np.array([])
    n = len(v)
    if n < 4 or np.var(v) == 0:
        return float("nan")
    d_o = sum(float(((u[:, None] - u[None, :]) ** 2).sum()) / (len(u) - 1) for u in us) / n
    d_e = float(((v[:, None] - v[None, :]) ** 2).sum()) / (n * (n - 1))
    return 1 - d_o / d_e


def _bucket(x: float) -> int:
    return 0 if x <= -4 else 1 if x <= -2 else 2 if x <= 1 else 3 if x <= 3 else 4


def _metrics(pairs: list[tuple[float, float]], alpha: float | None = None) -> dict:
    if len(pairs) < 5:
        return {"n": len(pairs)}
    a, b = np.array(pairs).T
    return {
        "n": len(pairs),
        "pearson": round(float(st.pearsonr(a, b)[0]), 3) if np.std(a) and np.std(b) else None,
        "spearman": round(float(st.spearmanr(a, b)[0]), 3) if np.std(a) and np.std(b) else None,
        "mae": round(float(np.mean(np.abs(a - b))), 2),
        "bucket_acc": round(float(np.mean([_bucket(x) == _bucket(y) for x, y in pairs])), 3),
        "alpha": round(alpha_interval(a, b) if alpha is None else alpha, 3),
    }


def _pairs(human: dict[str, dict], other: dict[str, dict], h_key: str, o_key: str) -> list[tuple[float, float]]:
    pairs = [(_num(h.get(h_key)), _num(other[i].get(o_key))) for i, h in human.items() if i in other and not h.get("skipped")]
    return [(x, y) for x, y in pairs if x is not None and y is not None]


def compare(human: dict[str, dict], other: dict[str, dict], h_key: str, o_key: str) -> dict:
    return _metrics(_pairs(human, other, h_key, o_key))


def compare_pooled(humans: dict[str, dict[str, dict]], other: dict[str, dict], h_key: str, o_key: str) -> dict:
    """Several coders: every human label is paired with the system's score for the same article, so
    model–human pairs hold one human label each, like human–human pairs."""
    return _metrics([p for labels in humans.values() for p in _pairs(labels, other, h_key, o_key)])


def human_human(humans: dict[str, dict[str, dict]], h_key: str) -> dict:
    """Agreement between the coders of the same article; n = articles with two or more labels.
    Pearson and Spearman use every pair in both orders, so they don't depend on which coder counts
    as first; α is Krippendorff's over all coders."""
    values: dict[str, list[float]] = defaultdict(list)
    for labels in humans.values():
        for i, h in labels.items():
            x = None if h.get("skipped") else _num(h.get(h_key))
            if x is not None:
                values[i].append(x)
    units = [v for v in values.values() if len(v) >= 2]
    if len(units) < 5:
        return {"n": len(units)}
    return {**_metrics([p for u in units for p in itertools.permutations(u, 2)], alpha=alpha_units(units)), "n": len(units)}


def _coders(set_name: str, coder: str) -> list[str]:
    """'all': every coder with a labels file for this set (validation/labels/{set}-{coder}.jsonl),
    skipping files of longer set names such as {set}-2."""
    if coder != "all":
        return [c.strip() for c in coder.split(",") if c.strip()]
    longer = [p.stem for p in (data_dir() / sample.SETS).glob(f"{set_name}-*.json")]
    files = sorted((data_dir() / "validation" / "labels").glob(f"{set_name}-*.jsonl"))
    return [p.stem[len(set_name) + 1:] for p in files if not any(p.stem.startswith(f"{s}-") for s in longer)]


def _row(name: str, m: dict) -> str:
    return f"| {name} | {m.get('n')} | {m.get('pearson', '–')} | {m.get('spearman', '–')} | {m.get('mae', '–')} | {m.get('bucket_acc', '–')} | {m.get('alpha', '–')} |"


def report(set_name: str, coder: str, specs: list[str], coder2: str | None = None) -> str:
    humans = {c: load_labels(set_name, c) for c in _coders(set_name, coder)}
    pooled = len(humans) > 1
    ids = sorted({i for labels in humans.values() for i in labels})
    systems: dict[str, dict[str, dict]] = {"production": _production(ids)}
    for s in specs:
        systems[s] = _load_preds(set_name, s)
    if coder2 and not pooled:
        h2 = load_labels(set_name, coder2)
        systems[f"human:{coder2}"] = {i: {**d, "china_stance": d.get("china")} for i, d in h2.items()}
    n_labels = sum(1 for labels in humans.values() for d in labels.values() if not d.get("skipped"))
    lines = [f"# 驗證報告：{set_name}（標註者 {', '.join(humans) or '無'}，{len(ids)} 篇" + (f"、{n_labels} 份標註" if pooled else "") + "）", "",
             "MBD 對照：lean 模型–人 Pearson r = 0.800、人–人 0.756；tone 模型–人 0.786、人–人 0.772。", ""]
    if pooled:
        lines += ["多位標註者：模型與每一份人工標註配對比較（n = 標註份數）；「人–人」是同一篇文章的人工標註兩兩比較"
                  "（n = 有兩份以上標註的篇數，α 為 Krippendorff，允許缺漏）。", ""]
    for label, h_key in (("綠藍 lean", "lean"), ("抗中友中 china", "china"), ("語氣 tone", "tone")):
        o_key = {"lean": "lean", "china": "china_stance", "tone": "tone"}[h_key]
        lines += [f"## {label}", "", "| 系統 | n | Pearson | Spearman | MAE | 五級準確率 | Krippendorff α |", "|---|---|---|---|---|---|---|"]
        for name, preds in systems.items():
            m = compare_pooled(humans, preds, h_key, o_key) if pooled else compare(next(iter(humans.values()), {}), preds, h_key, o_key)
            lines.append(_row(name, m))
        if pooled:
            lines.append(_row("人–人", human_human(humans, h_key)))
        lines.append("")
    lines += ["## 每篇成本（美元）", ""]
    for s in specs:
        path = _pred_path(set_name, s)
        if path.exists():
            costs = [json.loads(ln).get("cost_usd", 0) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            lines.append(f"- {s}：平均 {np.mean(costs):.5f}，每月約 {np.mean(costs) * 800 * 30:.1f}（以每天 800 篇、flex/batch 半價計）")
    text = "\n".join(lines) + "\n"
    out = data_dir() / "validation" / "reports" / f"{lake.taipei_today().isoformat()}-{set_name}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return text
