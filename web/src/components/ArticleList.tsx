import { useMemo, useState } from "react";
import type { Article, Axis, Meta } from "../types";
import { outletName, shortTime, signed } from "../format";
import { scoreColor, type Theme } from "../palette";

function Chip({ axis, label, value, theme }: { axis: Axis; label: string; value: number | null; theme: Theme }) {
  if (value == null) return null;
  return (
    <span className="chip" title={`${label} ${signed(value, 0)}`}>
      <i style={{ background: scoreColor(theme, axis, value) }} />
      {label} {signed(value, 0)}
    </span>
  );
}

export default function ArticleList({ articles, meta, theme, pageSize = 40 }: { articles: Article[]; meta: Meta; theme: Theme; pageSize?: number }) {
  const [outlet, setOutlet] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [shown, setShown] = useState(pageSize);
  const outlets = useMemo(() => meta.outlets.filter((o) => articles.some((a) => a.outlet === o.id)), [meta, articles]);
  const rows = outlet ? articles.filter((a) => a.outlet === outlet) : articles;

  return (
    <>
      <div className="controls">
        <select value={outlet} onChange={(e) => { setOutlet(e.target.value); setShown(pageSize); }} aria-label="篩選媒體">
          <option value="">全部媒體（{articles.length}）</option>
          {outlets.map((o) => (
            <option key={o.id} value={o.id}>
              {o.short}（{articles.filter((a) => a.outlet === o.id).length}）
            </option>
          ))}
        </select>
      </div>
      <ul className="articles">
        {rows.slice(0, shown).map((a) => (
          <li key={a.id}>
            <div className="title">
              <a href={a.url} target="_blank" rel="noopener noreferrer">
                {a.title}
              </a>
            </div>
            <div className="meta">
              <span>{outletName(meta, a.outlet)}</span>
              <span>{shortTime(a.published)}</span>
              {a.type && a.type !== "新聞報導" && <span className="badge">{a.type}</span>}
              {a.wire && <span className="badge" title="與中央社稿件高度相似">轉載中央社</span>}
              <Chip axis="lean" label="綠藍" value={a.lean} theme={theme} />
              {a.china_rel ? <Chip axis="china" label="抗中友中" value={a.china} theme={theme} /> : null}
              <Chip axis="tone" label="語氣" value={a.tone} theme={theme} />
              {(a.takeaways.length > 0 || a.lean_reason) && (
                <button type="button" className="linkbtn" onClick={() => setOpen(open === a.id ? null : a.id)} aria-expanded={open === a.id}>
                  {open === a.id ? "收合" : "重點與理由"}
                </button>
              )}
            </div>
            {open === a.id && (
              <div className="details">
                {a.takeaways.length > 0 && (
                  <ul>
                    {a.takeaways.map((t, i) => (
                      <li key={i}>{t}</li>
                    ))}
                  </ul>
                )}
                {a.lean_reason && (
                  <p>
                    <b>綠藍判斷：</b>
                    {a.lean_reason}
                  </p>
                )}
                {a.china_reason && (
                  <p>
                    <b>對中立場判斷：</b>
                    {a.china_reason}
                  </p>
                )}
                <p className="note">重點與理由由 AI 模型產生，僅供參考。</p>
              </div>
            )}
          </li>
        ))}
      </ul>
      {rows.length > shown && (
        <button type="button" className="linkbtn" onClick={() => setShown(shown + pageSize)}>
          顯示更多（還有 {rows.length - shown} 篇）
        </button>
      )}
    </>
  );
}
