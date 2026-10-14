import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useData, useMeta, usePageTitle, useWindowKey } from "../data";
import { PAGES } from "../site";
import type { Axis, IndexDoc, UnitDoc, UnitKind } from "../types";
import { AXIS_LABEL, num } from "../format";
import { useTheme } from "../palette";
import { StackedBuckets } from "../components/Charts";
import { Empty, ErrorState, Loading } from "../components/States";

export default function Coverage() {
  const [w] = useWindowKey();
  const theme = useTheme();
  const nav = useNavigate();
  const [unit, setUnit] = useState("all/all");
  const [axis, setAxis] = useState<Axis>("lean");
  const meta = useMeta();
  const index = useData<IndexDoc>("index.json");
  const doc = useData<UnitDoc>(`units/${unit}.json`);
  usePageTitle(PAGES.coverage.title);
  if (meta.error || index.error) return <ErrorState error={(meta.error ?? index.error)!} />;
  if (!meta.data || !index.data) return <Loading />;
  const m = meta.data;
  const idx = index.data;
  const win = doc.data?.windows[w];
  const groups: [UnitKind, string][] = [["category", "類別"], ["topic", "議題"], ["subtopic", "子議題"]];

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>報導量與立場分布</h1>
          <p>
            每家媒體在這段期間的報導數，依立場分組堆疊（仿 Media Bias Detector 的長條圖）。媒體依近 90 天的平均綠藍傾向由綠到藍排序。
          </p>
        </div>
      </div>
      <section className="card">
        <div className="controls">
          <select value={unit} onChange={(e) => setUnit(e.target.value)} aria-label="選擇議題">
            <option value="all/all">所有政治新聞</option>
            {groups.map(([kind, label]) => (
              <optgroup key={kind} label={label}>
                {(idx[kind] ?? [])
                  .filter((e) => e.windows[w]?.n)
                  .map((e) => (
                    <option key={e.id} value={`${kind}/${e.id}`}>
                      {e.name}（{e.windows[w].n}）
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
          <div className="seg" role="group" aria-label="立場軸">
            {(["lean", "china", "tone"] as Axis[]).map((a) => (
              <button key={a} type="button" aria-pressed={axis === a} onClick={() => setAxis(a)}>
                {AXIS_LABEL[a]}
              </button>
            ))}
          </div>
        </div>
        {doc.error && <ErrorState error={doc.error} />}
        {doc.loading && <Loading />}
        {!doc.loading && !win && <Empty>這段期間沒有相關報導。</Empty>}
        {win && (
          <>
            <div className="sub">
              {num(win.n)} 篇報導，其中 {num(win.n_scored)} 篇已完成評分
              {axis === "china" ? "；抗中友中只計與中國相關的報導" : ""}。
            </div>
            <StackedBuckets
              outlets={m.outlets}
              stats={win.outlets}
              axis={axis}
              names={m.buckets[axis]}
              theme={theme}
              onPick={(id) => nav(`/outlet/${id}?w=${w}`)}
            />
            {m.outlets.some((o) => o.via === "yahoo") && <p className="note">* 經 Yahoo 奇摩新聞轉載取得，僅含被轉載的文章。</p>}
          </>
        )}
      </section>
    </div>
  );
}
