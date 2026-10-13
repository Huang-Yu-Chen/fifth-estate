import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useData, useMeta, useWindowKey } from "../data";
import type { GridDoc } from "../types";
import { signed } from "../format";
import { useTheme } from "../palette";
import EChart, { type EChartsOption } from "../components/EChart";
import { Empty, ErrorState, Loading } from "../components/States";

type Metric = "n" | "lean" | "china" | "tone";
const METRICS: [Metric, string][] = [
  ["n", "報導數"],
  ["lean", "綠藍平均"],
  ["china", "抗中友中平均"],
  ["tone", "語氣平均"],
];

export default function Grid() {
  const [w] = useWindowKey();
  const theme = useTheme();
  const nav = useNavigate();
  const [metric, setMetric] = useState<Metric>("n");
  const meta = useMeta();
  const grid = useData<GridDoc>("grid.json");

  const option = useMemo<EChartsOption | null>(() => {
    if (!meta.data || !grid.data) return null;
    const g = grid.data[w] ?? {};
    const outlets = meta.data.outlets.filter((o) => g[o.id]).reverse();
    const topics = meta.data.topics.filter((t) => outlets.some((o) => g[o.id]?.[t.id]?.n));
    const data: (number | null | string)[][] = [];
    let maxN = 1;
    topics.forEach((t, x) =>
      outlets.forEach((o, y) => {
        const c = g[o.id]?.[t.id];
        if (!c) return;
        const v = metric === "n" ? c.n : c[metric];
        if (metric === "n") maxN = Math.max(maxN, c.n);
        data.push([x, y, v == null ? "-" : v, c.n, t.id]);
      }),
    );
    const diverging = metric !== "n";
    const axis = metric === "china" ? "china" : metric === "tone" ? "tone" : "lean";
    return {
      animation: false,
      tooltip: {
        backgroundColor: theme.surface,
        borderColor: theme.line,
        textStyle: { color: theme.text, fontSize: 12 },
        formatter: (p: { data: [number, number, number | string, number] }) => {
          const [x, y, v, n] = p.data;
          const val = metric === "n" ? `${v} 篇` : `${typeof v === "number" ? signed(v) : "—"}（${n} 篇）`;
          return `${outlets[y].short} × ${topics[x].name}<br/>${val}`;
        },
      },
      grid: { left: 8, right: 16, top: 8, bottom: 8, containLabel: true },
      xAxis: { type: "category", data: topics.map((t) => t.name), axisLabel: { color: theme.muted, rotate: 45, fontSize: 11 }, axisLine: { lineStyle: { color: theme.line } }, splitArea: { show: false } },
      yAxis: { type: "category", data: outlets.map((o) => o.short + (o.via ? "*" : "")), axisLabel: { color: theme.text }, axisLine: { lineStyle: { color: theme.line } } },
      visualMap: diverging
        ? { min: -3, max: 3, dimension: 2, calculable: false, orient: "horizontal", left: "center", bottom: 0, show: false, inRange: { color: theme.buckets[axis] } }
        : { min: 0, max: maxN, dimension: 2, show: false, inRange: { color: [theme.dark ? "#26243a" : "#f1ecfb", theme.accent] } },
      series: [
        {
          type: "heatmap",
          data,
          label: { show: true, fontSize: 11, color: theme.text, formatter: (p: { data: [number, number, number | string] }) => (typeof p.data[2] === "number" ? (metric === "n" ? String(p.data[2]) : signed(p.data[2])) : "") },
          itemStyle: { borderColor: theme.surface, borderWidth: 2 },
          emphasis: { itemStyle: { borderColor: theme.text, borderWidth: 1 } },
        },
      ],
    };
  }, [meta.data, grid.data, w, metric, theme]);

  if (meta.error || grid.error) return <ErrorState error={(meta.error ?? grid.error)!} />;
  if (!meta.data || !grid.data) return <Loading />;
  const g = grid.data[w] ?? {};
  const nOutlets = meta.data.outlets.filter((o) => g[o.id]).length;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>媒體 × 議題</h1>
          <p>每一格是某家媒體在某個議題的報導數或平均立場（仿 Media Bias Detector 的格狀圖）。點選格子查看該議題。</p>
        </div>
      </div>
      <section className="card">
        <div className="controls">
          <div className="seg" role="group" aria-label="指標">
            {METRICS.map(([k, label]) => (
              <button key={k} type="button" aria-pressed={metric === k} onClick={() => setMetric(k)}>
                {label}
              </button>
            ))}
          </div>
          {metric !== "n" && <span className="note">色階：{metric === "lean" ? "綠（親綠）→ 灰 → 藍（親藍）" : metric === "china" ? "青（抗中）→ 灰 → 橘（友中）" : "紫（負面）→ 灰 → 黃（正面）"}，以 ±3 為滿色</span>}
        </div>
        {!option || nOutlets === 0 ? (
          <Empty>這段期間沒有資料。</Empty>
        ) : (
          <EChart
            option={option}
            height={nOutlets * 34 + 150}
            minWidth={760}
            ariaLabel="媒體與議題的熱度圖"
            onClick={(p) => {
              const d = p.data as unknown[] | undefined;
              if (d && typeof d[4] === "string") nav(`/unit/topic/${d[4]}?w=${w}`);
            }}
          />
        )}
      </section>
    </div>
  );
}
