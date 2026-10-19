import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useData, useMeta, usePageTitle, useWindowKey } from "../data";
import type { Axis, UnitDoc, UnitKind } from "../types";
import { AXIS_LABEL, KIND_LABEL, num, shortDate, skewSentence, topicName } from "../format";
import { useTheme } from "../palette";
import { PolarizationHero } from "../components/Polarization";
import { Forest, Spectrum, StackedBuckets, Trend, type SpectrumPoint } from "../components/Charts";
import OutletTable from "../components/OutletTable";
import ArticleList from "../components/ArticleList";
import { Empty, ErrorState, Loading } from "../components/States";

export default function Unit() {
  const { kind = "all", id = "all" } = useParams<{ kind: UnitKind; id: string }>();
  const [w] = useWindowKey();
  const theme = useTheme();
  const nav = useNavigate();
  const [noOpinion, setNoOpinion] = useState(false);
  const [axis, setAxis] = useState<Axis>("lean");
  const meta = useMeta();
  const doc = useData<UnitDoc>(`units/${kind}/${encodeURIComponent(id)}`);
  usePageTitle(doc.data?.name);
  if (meta.error) return <ErrorState error={meta.error} />;
  if (doc.error) return <Empty>找不到這個{KIND_LABEL[kind as UnitKind] ?? "項目"}，可能已經整併或超出資料期間。</Empty>;
  if (!meta.data || !doc.data) return <Loading />;
  const m = meta.data;
  const d = doc.data;
  const isEvent = kind === "event";
  const winKey = isEvent ? "event" : w;
  const win = d.windows[winKey];
  const pol = win && noOpinion && win.no_opinion ? win.no_opinion : win?.polarization;

  const crumbs: { to?: string; label: string }[] = [{ to: `/?w=${w}`, label: "總覽" }];
  if (d.kind === "topic" && d.category) crumbs.push({ label: d.category });
  if (d.kind === "subtopic" && d.topic) crumbs.push({ to: `/unit/topic/${d.topic}?w=${w}`, label: d.topic_name ?? topicName(m, d.topic) });
  if (d.kind === "event" && d.topic) crumbs.push({ to: `/unit/topic/${d.topic}?w=${w}`, label: topicName(m, d.topic) });

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="crumbs">
            {crumbs.map((c, i) => (
              <span key={i}>
                {c.to ? <Link to={c.to}>{c.label}</Link> : c.label}
                {" › "}
              </span>
            ))}
            {KIND_LABEL[d.kind]}
          </div>
          <h1>
            {d.name} {d.short_title && <span className="badge accent">{d.short_title}</span>}
          </h1>
          {d.description && <p>{d.description}</p>}
          {isEvent && (
            <p>
              {shortDate(d.first)}–{shortDate(d.last)} 期間的報導（事件依 3 天滑動視窗持續更新）。
            </p>
          )}
          {d.origin === "auto" && <p className="note">這個子議題由系統依報導內容自動建立，名稱會定期人工審核。</p>}
        </div>
      </div>

      {!win ? (
        <Empty>近 {w} 天沒有這個議題的報導，試試較長的期間。</Empty>
      ) : (
        <>
          <section className="card">
            <div className="controls" style={{ justifyContent: "space-between" }}>
              <h2>極化指數</h2>
              {win.no_opinion !== undefined && (
                <label className="note">
                  <input type="checkbox" checked={noOpinion} onChange={(e) => setNoOpinion(e.target.checked)} /> 排除評論文章
                </label>
              )}
            </div>
            {pol ? <PolarizationHero pol={pol} /> : <p className="note">排除評論後沒有足夠的報導。</p>}
            <p className="note" style={{ marginTop: 12 }}>
              {skewSentence(win.attention_skew)} 指數比較的是各家媒體報導立場之間的差距：各家一致為 0；媒體平分兩群、各站一個極端為 100。
            </p>
          </section>

          {!isEvent && d.trend && d.trend.length > 0 && (
            <section className="card">
              <h2>趨勢</h2>
              <div className="sub">每日的 7 日滾動極化指數（樣本不足的日子留白）。</div>
              <Trend points={d.trend} theme={theme} />
            </section>
          )}

          <div className="grid-2">
            <section className="card">
              <h2>媒體光譜</h2>
              <div className="sub">每家媒體在這個{KIND_LABEL[d.kind]}的平均位置；圓越大報導越多，線段為 95% 信賴區間。虛線圈表示涉中報導不足。</div>
              {(() => {
                const pts: SpectrumPoint[] = m.outlets
                  .filter((o) => win.outlets[o.id]?.lean != null)
                  .map((o) => {
                    const s = win.outlets[o.id];
                    return { id: o.id, label: o.short, x: s.lean, y: s.china_n >= 3 ? s.china : null, n: s.n, xci: s.lean_ci, yci: s.china_ci };
                  });
                const twoD = pts.filter((p) => p.y != null).length >= 3;
                return twoD ? (
                  <Spectrum theme={theme} points={pts} onPick={(oid) => nav(`/outlet/${oid}?w=${w}`)} />
                ) : (
                  <>
                    <p className="note">這個{KIND_LABEL[d.kind]}的涉中報導不足，只呈現綠藍軸。</p>
                    <Forest theme={theme} axis="lean" points={pts} onPick={(oid) => nav(`/outlet/${oid}?w=${w}`)} />
                  </>
                );
              })()}
            </section>
            <section className="card">
              <div className="controls" style={{ justifyContent: "space-between" }}>
                <h2>立場分布</h2>
                <div className="seg" role="group" aria-label="立場軸">
                  {(["lean", "china", "tone"] as Axis[]).map((a) => (
                    <button key={a} type="button" aria-pressed={axis === a} onClick={() => setAxis(a)}>
                      {AXIS_LABEL[a]}
                    </button>
                  ))}
                </div>
              </div>
              <StackedBuckets outlets={m.outlets} stats={win.outlets} axis={axis} names={m.buckets[axis]} theme={theme} onPick={(oid) => nav(`/outlet/${oid}?w=${w}`)} />
            </section>
          </div>

          <section className="card">
            <h2>各媒體指標</h2>
            <div className="sub">
              {num(win.n)} 篇報導（{num(win.n_scored)} 篇已評分）。綠藍：−5 強烈支持民進黨 … +5 強烈支持國民黨；抗中友中：−5 強烈抗中 … +5 強烈友中。
            </div>
            <OutletTable meta={m} stats={win.outlets} theme={theme} w={w} showShare={!isEvent} />
          </section>
        </>
      )}

      <section className="card">
        <h2>報導</h2>
        <div className="sub">由新到舊。標題連到原始報導；分數與理由由 AI 標註。</div>
        <ArticleList key={`${kind}/${id}`} unit={`${kind}/${id}`} meta={m} theme={theme} />
      </section>
    </div>
  );
}
