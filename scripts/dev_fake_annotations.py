"""DEV ONLY: fill a *local* data dir with fake Stage 1/2 labels and embeddings so the metrics,
export and dashboard can be developed before an API key exists. Every row is tagged model=FAKE.

    FIFTH_DATA_DIR=../fifth-estate-data-local uv run python scripts/dev_fake_annotations.py

Refuses to run against the real data repo checkout.
"""

from __future__ import annotations

import os
import random
import re
import sys

import numpy as np

from fifth_estate.config import data_dir
from fifth_estate.store import lake

if "local" not in str(data_dir()):
    sys.exit(f"refusing to write fake data into {data_dir()} (path must contain 'local')")

rng = random.Random(7)
np_rng = np.random.default_rng(7)
PRIOR = {"ltn": (-2.2, -2.5), "setn": (-2.4, -2.2), "ftv": (-2.6, -2.4), "newtalk": (-2.0, -2.6), "mirror": (-0.6, -1.0),
         "cna": (-0.4, -0.8), "pts": (-0.2, -0.6), "ettoday": (0.3, 0.0), "ebc": (0.4, 0.2), "storm": (0.6, 0.4),
         "tvbs": (1.0, 0.6), "udn": (1.6, 1.4), "chinatimes": (2.6, 2.8), "cti": (3.0, 3.2)}
RULES = [
    (r"沈伯洋|蔣萬安|北市", "國內政治", "elections", "taipei_mayor_2026"),
    (r"蘇巧慧|李四川|新北", "國內政治", "elections", "newtaipei_mayor_2026"),
    (r"江啟臣|何欣純|台中", "國內政治", "elections", "taichung_mayor_2026"),
    (r"黃世杰|張善政|桃園", "國內政治", "elections", "taoyuan_mayor_2026"),
    (r"選|參選|民調|競總", "國內政治", "elections", "county_races_2026"),
    (r"國慶|山巔之城|假民主", "國內政治", "governance", "national_day_2026"),
    (r"巴拉圭|貝里斯|友邦|邦交", "外交", "allies_participation", "prc_pressure_on_allies"),
    (r"軍演|共軍|解放軍|共機", "兩岸", "pla_activity", "prc_drills"),
    (r"無人機|F-16|國防|飛官|國軍|軍購|206旅", "兩岸", "defense_policy", "military_personnel"),
    (r"川普|美國|美方", "外交", "us_relations", "trump_xi_taiwan"),
    (r"松機|松山機場", "國內政治", "economy_livelihood", "songshan_airport_relocation"),
    (r"陸|中國|北京|兩岸", "兩岸", "sovereignty", "other"),
]
arts = lake.rows(f"SELECT * FROM {lake.source('articles')} QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at) = 1")
run_id = lake.new_run_id()
s1, s2, emb = [], [], []
centers: dict[str, np.ndarray] = {}
for a in arts:
    text = a["title"] + a["body"][:300]
    scope, topic, sub = "與台灣無關的國際新聞", "none", "none"
    for pat, sc, tp, sb in RULES:
        if re.search(pat, text):
            scope, topic, sub = sc, tp, sb
            break
    if scope == "與台灣無關的國際新聞" and not re.search(r"國際|全球|world", a["section"] or ""):
        scope, topic, sub = "國內政治", "governance", "other"
    sents = [s for s in re.split(r"(?<=。)", a["body"]) if s.strip()][:3]
    common = {"model": "FAKE", "reasoning_effort": None, "service_tier": "fake", "prompt_hash": "fake", "clean_version": "fake",
              "status": "ok", "cost_usd": 0.0, "created_at": lake.now_utc(), "run_id": run_id}
    s1.append({"article_id": a["article_id"], "scope_reason": "fake", "scope": scope, "takeaways": sents, "topic_id": topic,
               "subtopic_id": sub, "proposed_subtopic": "假子議題" if sub == "other" else None, "proposed_subtopic_desc": None,
               "news_type": rng.choice(["新聞報導"] * 6 + ["新聞分析", "評論"]), "news_type_reason": "fake", **common})
    if scope in ("國內政治", "外交", "兩岸"):
        lean_mu, china_mu = PRIOR.get(a["outlet_id"], (0, 0))
        rel = 2 if scope == "兩岸" else (1 if re.search(r"中國|陸|北京", text) else 0)
        clip = lambda x: int(max(-5, min(5, round(x))))  # noqa: E731
        s2.append({"article_id": a["article_id"], "tone_reason": "fake", "tone": clip(rng.gauss(-1, 2)), "title_tone_reason": "fake",
                   "title_tone": clip(rng.gauss(-1, 2)), "lean_reason": "（測試資料）依框架判斷", "lean": clip(rng.gauss(lean_mu, 1.5)),
                   "title_lean_reason": "fake", "title_lean": clip(rng.gauss(lean_mu, 2)), "china_relevance": rel,
                   "china_reason": "（測試資料）" if rel else "與中國無關", "china_stance": clip(rng.gauss(china_mu, 1.5)) if rel else None,
                   "title_china_reason": "fake", "title_china_stance": None, **common})
        key = sub if sub not in ("other", "none") else a["article_id"]
        if key not in centers:
            centers[key] = np_rng.normal(size=1024)
        v = centers[key] + np_rng.normal(scale=0.35, size=1024)
        v = v / np.linalg.norm(v)
        emb.append({"article_id": a["article_id"], "model": "FAKE", "dims": 1024, "text_hash": "fake",
                    "vector_f16": v.astype(np.float16).tobytes(), "input_tokens": 0, "cost_usd": 0.0,
                    "created_at": lake.now_utc(), "run_id": run_id})
lake.write("stage1", s1, run_id)
lake.write("stage2", s2, run_id)
lake.write("embeddings", emb, run_id)
print(f"fake labels: stage1={len(s1)} stage2={len(s2)} embeddings={len(emb)} in {data_dir()}")
os.environ.setdefault("FIFTH_FAKE", "1")
