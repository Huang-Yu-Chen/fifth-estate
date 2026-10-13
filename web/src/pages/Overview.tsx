import { Link, useNavigate } from "react-router-dom";
import { useData, useMeta, useWindowKey } from "../data";
import type { IndexDoc } from "../types";
import { num, shortDate, shortTime } from "../format";
import { useTheme } from "../palette";
import RankingList from "../components/RankingList";
import { Spectrum } from "../components/Charts";
import { ErrorState, Loading } from "../components/States";

export default function Overview() {
  const [w] = useWindowKey();
  const theme = useTheme();
  const nav = useNavigate();
  const meta = useMeta();
  const index = useData<IndexDoc>("index.json");
  if (meta.error || index.error) return <ErrorState error={(meta.error ?? index.error)!} />;
  if (!meta.data || !index.data) return <Loading />;
  const m = meta.data;
  const idx = index.data;
  const all = idx.all?.[0]?.windows[w];
  const events = [...(idx.event ?? [])].sort((a, b) => (b.last ?? "").localeCompare(a.last ?? "")).slice(0, 8);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>台灣新聞媒體的政治極化偵測器</h1>
          <p>
            每小時收集 {m.outlets.length} 家網路媒體的政治、兩岸與外交新聞，由 AI 依編碼手冊標註每篇報導的綠藍傾向、對中立場與語氣，
            計算各議題在媒體之間的極化程度。方法改編自賓州大學 Media Bias Detector，詳見<Link to={`/methodology?w=${w}`}>方法論</Link>。
          </p>
        </div>
      </div>

      <div className="kpis">
        <div className="kpi">
          <div className="label">政治新聞（近 {w} 天）</div>
          <div className="value">{num(all?.n ?? 0)}</div>
        </div>
        <div className="kpi">
          <div className="label">已完成立場評分</div>
          <div className="value">{num(all?.n_scored ?? 0)}</div>
        </div>
        <div className="kpi">
          <div className="label">整體極化指數</div>
          <div className="value">{all?.P == null ? "—" : num(all.P, 0)}</div>
        </div>
        <div className="kpi">
          <div className="label">資料更新</div>
          <div className="value" style={{ fontSize: 18, paddingTop: 6 }}>{shortTime(m.generated_at)}</div>
        </div>
      </div>

      <div className="grid-2">
        <section className="card">
          <h2>議題極化指數排行</h2>
          <div className="sub">0 表示各家媒體立場一致，100 表示媒體分成兩群、各站一個極端。灰色列為樣本不足。</div>
          <RankingList kind="topic" entries={idx.topic ?? []} w={w} />
        </section>
        <section className="card">
          <h2>子議題極化指數排行</h2>
          <div className="sub">子議題是持續一段時間的具體議題，例如各縣市首長選舉、國防特別預算。</div>
          <RankingList kind="subtopic" entries={idx.subtopic ?? []} w={w} />
        </section>
      </div>

      <div className="grid-2">
        <section className="card">
          <h2>媒體光譜</h2>
          <div className="sub">各媒體近 90 天所有政治新聞的平均位置。點選媒體查看細節；虛線圈表示涉中報導不足。</div>
          <Spectrum
            theme={theme}
            points={m.outlets.map((o) => ({ id: o.id, label: o.short, x: o.baseline_lean, y: o.baseline_china, n: o.n[w] }))}
            onPick={(id) => nav(`/outlet/${id}?w=${w}`)}
          />
        </section>
        <section className="card">
          <h2>近期事件</h2>
          <div className="sub">同一事件的報導依內容相似度自動分群（3 天滑動視窗）。</div>
          {events.length === 0 ? (
            <p className="note">還沒有足夠的報導形成事件。</p>
          ) : (
            <ul className="rank">
              {events.map((e) => {
                const ew = e.windows.event;
                return (
                  <li key={e.id} className={ew?.sufficient ? "" : "insufficient"}>
                    <span className="name">
                      <Link to={`/unit/event/${e.id}?w=${w}`}>{e.name}</Link>
                      <small>{shortDate(e.last)}</small>
                    </span>
                    <span className="note">
                      {num(ew?.n)} 篇 · {Object.keys(ew?.outlets ?? {}).length} 家
                    </span>
                    <span className="value">{ew?.P == null ? "—" : num(ew.P, 0)}</span>
                  </li>
                );
              })}
            </ul>
          )}
          <p style={{ marginTop: 10 }}>
            <Link to={`/events?w=${w}`}>看所有事件 →</Link>
          </p>
        </section>
      </div>
    </div>
  );
}
