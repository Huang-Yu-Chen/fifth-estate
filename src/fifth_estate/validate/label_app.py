"""Local labeling page: `fifth label --set gold --coder yuchen` then open http://127.0.0.1:8765.

Shows one article at a time, source-blinded exactly like the model input (no outlet, no model
scores). Labels are appended to {data_dir}/validation/labels/{set}-{coder}.jsonl.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..annotate.common import article_text
from ..config import data_dir
from ..store import lake
from . import sample

PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>第五權 標註</title>
<style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e3e2dc;--accent:#2f5d9e}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--muted:#a3a29b;--card:#20201e;--line:#34332f;--accent:#8ab0ea}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 -apple-system,"PingFang TC","Noto Sans TC",sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px;display:grid;grid-template-columns:1fr 340px;gap:20px}
@media (max-width:860px){main{grid-template-columns:1fr}}
article,form{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 20px}
article pre{white-space:pre-wrap;font:inherit;margin:0}
h1{font-size:15px;color:var(--muted);font-weight:500;margin:0 0 8px}
fieldset{border:0;padding:0;margin:0 0 14px}legend{font-weight:600;margin-bottom:4px}
.scale{display:flex;flex-wrap:wrap;gap:4px}.scale label{border:1px solid var(--line);border-radius:6px;padding:2px 7px;cursor:pointer}
.scale input{display:none}.scale input:checked+span{color:var(--accent);font-weight:700}
.hint{font-size:13px;color:var(--muted)}button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--accent);color:#fff;cursor:pointer}
button.secondary{background:transparent;color:var(--fg)}textarea{width:100%;font:inherit;background:transparent;color:var(--fg);border:1px solid var(--line);border-radius:6px}
</style></head><body><main>
<article><h1 id="progress"></h1><pre id="text">載入中…</pre></article>
<form id="f">
<fieldset><legend>範圍</legend><div class="scale" id="scope"></div></fieldset>
<fieldset><legend>綠藍傾向</legend><div class="hint">−5 強烈支持民進黨 … 0 無明顯傾向 … +5 強烈支持國民黨</div><div class="scale" id="lean"></div></fieldset>
<fieldset><legend>與中國相關程度</legend><div class="scale" id="china_relevance"></div></fieldset>
<fieldset><legend>抗中友中</legend><div class="hint">−5 強烈抗中 … 0 中立 … +5 強烈友中（與中國無關時免填）</div><div class="scale" id="china"></div></fieldset>
<fieldset><legend>語氣</legend><div class="hint">−5 非常負面 … +5 非常正面</div><div class="scale" id="tone"></div></fieldset>
<fieldset><legend>新聞類型</legend><div class="scale" id="news_type"></div></fieldset>
<fieldset><legend>備註</legend><textarea name="note" rows="2"></textarea></fieldset>
<button type="submit">儲存並下一篇</button> <button type="button" class="secondary" id="skip">跳過</button>
</form></main>
<script>
const opts={scope:["國內政治","外交","兩岸","不在範圍"],lean:[-5,-4,-3,-2,-1,0,1,2,3,4,5],china_relevance:[0,1,2],
china:[-5,-4,-3,-2,-1,0,1,2,3,4,5],tone:[-5,-4,-3,-2,-1,0,1,2,3,4,5],news_type:["新聞報導","新聞分析","評論"]};
for(const [k,vs] of Object.entries(opts)){document.getElementById(k).innerHTML=vs.map(v=>`<label><input type="radio" name="${k}" value="${v}"><span>${v}</span></label>`).join("")}
let cur=null;
async function next(){const r=await fetch("/api/next");const d=await r.json();
 if(!d.article_id){document.getElementById("text").textContent="這一組都標完了，謝謝！";document.getElementById("f").style.display="none";return}
 cur=d;document.getElementById("progress").textContent=`第 ${d.done+1} / ${d.total} 篇`;document.getElementById("text").textContent=d.text;
 document.getElementById("f").reset();window.scrollTo(0,0)}
async function save(skip){const fd=new FormData(document.getElementById("f"));const lab=Object.fromEntries(fd.entries());
 lab.article_id=cur.article_id;lab.skipped=!!skip;await fetch("/api/label",{method:"POST",body:JSON.stringify(lab)});next()}
document.getElementById("f").onsubmit=e=>{e.preventDefault();save(false)};document.getElementById("skip").onclick=()=>save(true);next();
</script></body></html>"""


def labels_path(set_name: str, coder: str) -> Path:
    p = data_dir() / "validation" / "labels" / f"{set_name}-{coder}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_labels(set_name: str, coder: str) -> dict[str, dict]:
    p = labels_path(set_name, coder)
    out = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                d = json.loads(ln)
                out[d["article_id"]] = d
    return out


def serve(set_name: str, coder: str, port: int = 8765) -> None:
    ids = sample.load(set_name)
    articles = {
        r["article_id"]: r
        for r in lake.rows(
            f"""SELECT article_id, outlet_id, title, body FROM {lake.source('articles')}
                QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at) = 1"""
        )
        if r["article_id"] in set(ids)
    }

    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a) -> None:  # quiet
            pass

        def do_GET(self) -> None:
            if self.path == "/":
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if self.path == "/api/next":
                done = load_labels(set_name, coder)
                todo = [i for i in ids if i not in done and i in articles]
                if not todo:
                    return self._send(200, b"{}", "application/json")
                a = articles[todo[0]]
                d = {"article_id": a["article_id"], "text": article_text(a), "done": len(done), "total": len(ids)}
                return self._send(200, json.dumps(d, ensure_ascii=False).encode(), "application/json")
            self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if self.path != "/api/label":
                return self._send(404, b"not found", "text/plain")
            lab = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            lab["coder"] = coder
            lab["labeled_at"] = lake.now_utc().isoformat()
            with labels_path(set_name, coder).open("a", encoding="utf-8") as f:
                f.write(json.dumps(lab, ensure_ascii=False) + "\n")
            self._send(200, b"{}", "application/json")

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"標註頁面：http://127.0.0.1:{port}  （{set_name}，{len(ids)} 篇；Ctrl+C 結束）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
