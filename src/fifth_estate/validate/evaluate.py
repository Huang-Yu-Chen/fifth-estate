"""Model bake-off and agreement report against human labels (MBD reports model–human vs human–human).

    fifth eval --set gold --coder yuchen --models gpt-6-luna:medium,gpt-6.1-sol:low,gpt-6-astra:low
"""

from __future__ import annotations

import asyncio
import json
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


def _bucket(x: float) -> int:
    return 0 if x <= -4 else 1 if x <= -2 else 2 if x <= 1 else 3 if x <= 3 else 4


def compare(human: dict[str, dict], other: dict[str, dict], h_key: str, o_key: str) -> dict:
    pairs = [(_num(h.get(h_key)), _num(other[i].get(o_key))) for i, h in human.items() if i in other and not h.get("skipped")]
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pairs) < 5:
        return {"n": len(pairs)}
    a, b = np.array(pairs).T
    return {
        "n": len(pairs),
        "pearson": round(float(st.pearsonr(a, b)[0]), 3) if np.std(a) and np.std(b) else None,
        "spearman": round(float(st.spearmanr(a, b)[0]), 3) if np.std(a) and np.std(b) else None,
        "mae": round(float(np.mean(np.abs(a - b))), 2),
        "bucket_acc": round(float(np.mean([_bucket(x) == _bucket(y) for x, y in pairs])), 3),
        "alpha": round(alpha_interval(a, b), 3),
    }


def report(set_name: str, coder: str, specs: list[str], coder2: str | None = None) -> str:
    human = load_labels(set_name, coder)
    ids = list(human)
    systems: dict[str, dict[str, dict]] = {"production": _production(ids)}
    for s in specs:
        systems[s] = _load_preds(set_name, s)
    if coder2:
        h2 = load_labels(set_name, coder2)
        systems[f"human:{coder2}"] = {i: {**d, "china_stance": d.get("china")} for i, d in h2.items()}
    lines = [f"# 驗證報告：{set_name}（標註者 {coder}，{len(ids)} 篇）", "",
             "MBD 對照：lean 模型–人 Pearson r = 0.800、人–人 0.756；tone 模型–人 0.786、人–人 0.772。", ""]
    for label, h_key in (("綠藍 lean", "lean"), ("抗中友中 china", "china"), ("語氣 tone", "tone")):
        o_key = {"lean": "lean", "china": "china_stance", "tone": "tone"}[h_key]
        lines += [f"## {label}", "", "| 系統 | n | Pearson | Spearman | MAE | 五級準確率 | Krippendorff α |", "|---|---|---|---|---|---|---|"]
        for name, preds in systems.items():
            m = compare(human, preds, h_key, o_key)
            lines.append(f"| {name} | {m.get('n')} | {m.get('pearson', '–')} | {m.get('spearman', '–')} | {m.get('mae', '–')} | {m.get('bucket_acc', '–')} | {m.get('alpha', '–')} |")
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
