import { Link } from "react-router-dom";
import { useData, useMeta, useWindowKey } from "../data";
import type { IndexEntry } from "../types";
import { num, outletName, shortDate, topicName } from "../format";
import { Empty, ErrorState, Loading } from "../components/States";

export default function Events() {
  const [w] = useWindowKey();
  const meta = useMeta();
  const events = useData<IndexEntry[]>("events.json");
  if (meta.error || events.error) return <ErrorState error={(meta.error ?? events.error)!} />;
  if (!meta.data || !events.data) return <Loading />;

  const byDay = new Map<string, IndexEntry[]>();
  for (const e of events.data) {
    const d = (e.last ?? "").slice(0, 10);
    if (!byDay.has(d)) byDay.set(d, []);
    byDay.get(d)!.push(e);
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>每日事件</h1>
          <p>
            依 Media Bias Detector 的作法，把 3 天內內容相似（嵌入向量 cosine ≥ {meta.data.params.events.tau}）的報導連成同一事件，
            至少 {meta.data.params.events.min_size} 篇才成立；事件標題由 AI 以中性用語產生。
          </p>
        </div>
      </div>
      {events.data.length === 0 && <Empty>還沒有事件。</Empty>}
      {[...byDay.entries()].map(([day, list]) => (
        <section key={day} className="events-day">
          <h3>{shortDate(day)} 最後更新</h3>
          <div className="grid-2">
            {list
              .sort((a, b) => (b.windows.event?.n ?? 0) - (a.windows.event?.n ?? 0))
              .map((e) => {
                const ew = e.windows.event;
                return (
                  <Link key={e.id} to={`/unit/event/${e.id}?w=${w}`} className="card event-card">
                    <div className="t">
                      {e.name} {e.short_title && <span className="badge accent">{e.short_title}</span>}
                    </div>
                    <div className="m">
                      {shortDate(e.first)}–{shortDate(e.last)} · {num(ew?.n)} 篇 · {Object.keys(ew?.outlets ?? {}).length} 家媒體
                      {e.topic && ` · ${topicName(meta.data, e.topic)}`} · 極化指數 {ew?.P == null ? "—" : num(ew.P, 0)}
                      {ew && !ew.sufficient && "（樣本不足）"}
                    </div>
                    <div className="outlet-chips">
                      {Object.entries(ew?.outlets ?? {})
                        .sort((a, b) => b[1] - a[1])
                        .map(([o, n]) => (
                          <span key={o} className="chip">
                            {outletName(meta.data, o)} {n}
                          </span>
                        ))}
                    </div>
                  </Link>
                );
              })}
          </div>
        </section>
      ))}
    </div>
  );
}
