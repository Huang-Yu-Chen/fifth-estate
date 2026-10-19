import { useEffect, useRef, useState } from "react";
import { loadArticles } from "../data";
import type { Article, Axis, Meta } from "../types";
import { num, outletName, shortTime, signed } from "../format";
import { scoreColor, type Theme } from "../palette";
import { Empty, ErrorState, Loading } from "./States";

function Chip({ axis, label, value, theme }: { axis: Axis; label: string; value: number | null; theme: Theme }) {
  if (value == null) return null;
  return (
    <span className="chip" title={`${label} ${signed(value, 0)}`}>
      <i style={{ background: scoreColor(theme, axis, value) }} />
      {label} {signed(value, 0)}
    </span>
  );
}

/**
 * A unit's articles (unit="topic/recall"), or one outlet's (outlet="cna"), newest first, loaded from the API
 * a page at a time. Give it a `key` per unit so the outlet filter starts over on a new unit.
 */
export default function ArticleList({
  meta,
  theme,
  unit = "all/all",
  outlet: only,
  pageSize = 40,
}: {
  meta: Meta;
  theme: Theme;
  unit?: string;
  outlet?: string;
  pageSize?: number;
}) {
  const [picked, setPicked] = useState("");
  const outlet = only ?? picked;
  const [open, setOpen] = useState<string | null>(null);
  const [articles, setArticles] = useState<Article[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [counts, setCounts] = useState<{ total: number; outlets: Record<string, number> } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const query = useRef("");

  useEffect(() => {
    const q = (query.current = `${unit}|${outlet}`);
    setLoading(true);
    setError(null);
    setArticles([]);
    setNext(null);
    loadArticles({ unit, outlet, limit: pageSize }).then(
      (p) => {
        if (query.current !== q) return;
        setArticles(p.articles);
        setNext(p.next);
        if (p.total != null) setCounts({ total: p.total, outlets: p.outlets ?? {} });
        setLoading(false);
      },
      (e: Error) => {
        if (query.current !== q) return;
        setError(e);
        setLoading(false);
      },
    );
  }, [unit, outlet, pageSize]);

  const more = () => {
    const q = query.current;
    setLoading(true);
    loadArticles({ unit, outlet, limit: pageSize, cursor: next }).then(
      (p) => {
        if (query.current !== q) return;
        setArticles((a) => [...a, ...p.articles]);
        setNext(p.next);
        setLoading(false);
      },
      (e: Error) => {
        if (query.current !== q) return;
        setError(e);
        setLoading(false);
      },
    );
  };

  const total = counts && (outlet ? (counts.outlets[outlet] ?? 0) : counts.total);
  const left = total != null ? total - articles.length : null;

  return (
    <>
      {!only && counts && counts.total > 0 && (
        <div className="controls">
          <select value={picked} onChange={(e) => setPicked(e.target.value)} aria-label="篩選媒體">
            <option value="">全部媒體（{num(counts.total)}）</option>
            {meta.outlets
              .filter((o) => counts.outlets[o.id])
              .map((o) => (
                <option key={o.id} value={o.id}>
                  {o.short}（{num(counts.outlets[o.id])}）
                </option>
              ))}
          </select>
        </div>
      )}
      {error && <ErrorState error={error} />}
      {!error && !loading && articles.length === 0 && <Empty>沒有報導。</Empty>}
      <ul className="articles">
        {articles.map((a) => (
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
      {loading && <Loading />}
      {!loading && !error && next && (
        <button type="button" className="linkbtn" onClick={more}>
          顯示更多{left != null && left > 0 ? `（還有 ${num(left)} 篇）` : ""}
        </button>
      )}
    </>
  );
}
