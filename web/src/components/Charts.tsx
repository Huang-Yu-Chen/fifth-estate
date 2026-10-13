import { useMemo } from "react";
import type { CustomSeriesRenderItemAPI, CustomSeriesRenderItemParams } from "echarts";
import EChart, { type EChartsOption } from "./EChart";
import { scoreColor, type Theme } from "../palette";
import { AXIS_ENDS, AXIS_LABEL, signed } from "../format";
import type { Axis, OutletMeta, OutletStats, TrendPoint } from "../types";

const MINUS = "−";

function axisStyle(t: Theme) {
  return {
    axisLine: { lineStyle: { color: t.line } },
    axisTick: { lineStyle: { color: t.line } },
    axisLabel: { color: t.muted },
    splitLine: { lineStyle: { color: t.line, opacity: 0.6 } },
    nameTextStyle: { color: t.muted },
  };
}

function tooltip(t: Theme) {
  return {
    backgroundColor: t.surface,
    borderColor: t.line,
    textStyle: { color: t.text, fontSize: 12 },
    confine: true,
  };
}

// ---------------------------------------------------------------- spectrum scatter

export interface SpectrumPoint {
  id: string;
  label: string;
  x: number | null;
  y: number | null;
  n?: number;
  xci?: number | null;
  yci?: number | null;
}

function range(vals: number[]): number {
  const m = Math.max(3, ...vals.map((v) => Math.abs(v) + 0.8));
  return Math.min(5, Math.ceil(m));
}

export function Spectrum({ points, theme: t, height = 380, onPick }: { points: SpectrumPoint[]; theme: Theme; height?: number; onPick?: (id: string) => void }) {
  const option = useMemo<EChartsOption>(() => {
    const pts = points.filter((p) => p.x != null);
    const rx = range(pts.map((p) => p.x as number));
    const ry = range(pts.filter((p) => p.y != null).map((p) => p.y as number));
    const maxN = Math.max(1, ...pts.map((p) => p.n ?? 1));
    const data = pts.map((p) => ({
      value: [p.x, p.y ?? 0, p.n ?? 0],
      name: p.label,
      id: p.id,
      symbolSize: p.n == null ? 14 : 8 + 22 * Math.sqrt((p.n ?? 0) / maxN),
      itemStyle: p.y == null
        ? { color: "transparent", borderColor: scoreColor(t, "lean", p.x), borderWidth: 2, borderType: "dashed" }
        : { color: scoreColor(t, "lean", p.x), borderColor: t.surface, borderWidth: 1, opacity: 0.9 },
      raw: p,
    }));
    const errData = pts.map((p) => [p.x, p.y ?? 0, p.xci ?? 0, p.y == null ? 0 : p.yci ?? 0]);
    const ends = (axis: Axis, r: number) => (v: number) =>
      v === -r ? `${MINUS}${r} ${AXIS_ENDS[axis][0]}` : v === r ? `+${r} ${AXIS_ENDS[axis][1]}` : v === 0 ? "0" : signed(v, 0);
    return {
      animation: false,
      grid: { left: 70, right: 30, top: 24, bottom: 52 },
      tooltip: {
        ...tooltip(t),
        formatter: (params: { data: { raw: SpectrumPoint } }) => {
          const p = params.data?.raw;
          if (!p) return "";
          const ln = `綠藍 ${signed(p.x)}${p.xci ? ` ± ${p.xci.toFixed(1)}` : ""}`;
          const cn = p.y == null ? "抗中友中：涉中報導不足" : `抗中友中 ${signed(p.y)}${p.yci ? ` ± ${p.yci.toFixed(1)}` : ""}`;
          return `<b>${p.label}</b><br/>${ln}<br/>${cn}${p.n != null ? `<br/>${p.n} 篇` : ""}`;
        },
      },
      xAxis: { type: "value", min: -rx, max: rx, name: "綠藍傾向", nameLocation: "middle", nameGap: 30, ...axisStyle(t), axisLabel: { color: t.muted, formatter: ends("lean", rx) } },
      yAxis: { type: "value", min: -ry, max: ry, name: "抗中友中", nameLocation: "middle", nameGap: 48, ...axisStyle(t), axisLabel: { color: t.muted, formatter: ends("china", ry) } },
      series: [
        {
          type: "custom",
          silent: true,
          z: 1,
          renderItem: (_: CustomSeriesRenderItemParams, api: CustomSeriesRenderItemAPI) => {
            const x = api.value(0) as number;
            const y = api.value(1) as number;
            const dx = api.value(2) as number;
            const dy = api.value(3) as number;
            const children = [];
            const style = { stroke: t.muted, lineWidth: 1, opacity: 0.6 };
            if (dx) {
              const a = api.coord([x - dx, y]);
              const b = api.coord([x + dx, y]);
              children.push({ type: "line", shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, style });
            }
            if (dy) {
              const a = api.coord([x, y - dy]);
              const b = api.coord([x, y + dy]);
              children.push({ type: "line", shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, style });
            }
            return { type: "group", children };
          },
          data: errData,
        },
        {
          type: "scatter",
          z: 3,
          data,
          label: { show: true, position: "right", formatter: "{b}", color: t.text, fontSize: 12 },
          labelLayout: { hideOverlap: true },
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { color: t.muted, type: "dashed", opacity: 0.5 },
            label: { show: false },
            data: [{ xAxis: 0 }, { yAxis: 0 }],
          },
        },
      ],
    };
  }, [points, t]);
  return (
    <EChart
      option={option}
      height={height}
      ariaLabel="媒體光譜圖：橫軸為綠藍傾向，縱軸為抗中友中立場"
      onClick={onPick ? (p) => { const d = p.data as { id?: string } | undefined; if (d?.id) onPick(d.id); } : undefined}
    />
  );
}

// ---------------------------------------------------------------- stacked bucket bars (MBD)

export function StackedBuckets({
  outlets,
  stats,
  axis,
  names,
  theme: t,
  onPick,
}: {
  outlets: OutletMeta[];
  stats: Record<string, OutletStats>;
  axis: Axis;
  names: string[];
  theme: Theme;
  onPick?: (outletId: string) => void;
}) {
  const option = useMemo<EChartsOption>(() => {
    const rows = outlets.filter((o) => stats[o.id]).reverse(); // ECharts draws category axes bottom-up
    const key = axis === "lean" ? "lean_buckets" : axis === "china" ? "china_buckets" : "tone_buckets";
    return {
      animation: false,
      tooltip: { ...tooltip(t), trigger: "axis", axisPointer: { type: "shadow" } },
      legend: { data: names, top: 0, textStyle: { color: t.muted, fontSize: 12 }, itemWidth: 12, itemHeight: 10 },
      grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
      xAxis: { type: "value", ...axisStyle(t), minInterval: 1 },
      yAxis: { type: "category", data: rows.map((o) => o.short + (o.via ? "*" : "")), ...axisStyle(t), axisLabel: { color: t.text } },
      series: names.map((name, i) => ({
        name,
        type: "bar",
        stack: "b",
        barMaxWidth: 22,
        itemStyle: { color: t.buckets[axis][i] },
        emphasis: { focus: "series" },
        data: rows.map((o) => ({ value: stats[o.id][key][i], id: o.id })),
      })),
    };
  }, [outlets, stats, axis, names, t]);
  const n = outlets.filter((o) => stats[o.id]).length;
  return (
    <EChart
      option={option}
      height={Math.max(160, n * 28 + 60)}
      ariaLabel={`各媒體報導的${AXIS_LABEL[axis]}分布`}
      onClick={onPick ? (p) => { const d = p.data as { id?: string } | undefined; if (d?.id) onPick(d.id); } : undefined}
    />
  );
}

// ---------------------------------------------------------------- trend

export function Trend({ points, theme: t, height = 240 }: { points: TrendPoint[]; theme: Theme; height?: number }) {
  const option = useMemo<EChartsOption>(
    () => ({
      animation: false,
      tooltip: { ...tooltip(t), trigger: "axis" },
      legend: { top: 0, textStyle: { color: t.muted, fontSize: 12 }, data: ["極化指數", "綠藍軸", "抗中友中軸"] },
      grid: { left: 36, right: 12, top: 34, bottom: 28 },
      xAxis: { type: "category", data: points.map((p) => p.date.slice(5).replace("-", "/")), ...axisStyle(t), splitLine: { show: false } },
      yAxis: { type: "value", min: 0, max: 100, ...axisStyle(t) },
      series: [
        { name: "極化指數", type: "line", data: points.map((p) => p.P), lineStyle: { width: 3, color: t.accent }, itemStyle: { color: t.accent }, symbolSize: 5, connectNulls: false },
        { name: "綠藍軸", type: "line", data: points.map((p) => p.P_lean ?? null), lineStyle: { width: 1.5, color: t.buckets.lean[4] }, itemStyle: { color: t.buckets.lean[4] }, symbolSize: 3 },
        { name: "抗中友中軸", type: "line", data: points.map((p) => p.P_china ?? null), lineStyle: { width: 1.5, type: "dashed", color: t.buckets.china[4] }, itemStyle: { color: t.buckets.china[4] }, symbolSize: 3 },
      ],
    }),
    [points, t],
  );
  return <EChart option={option} height={height} ariaLabel="極化指數趨勢（7 日滾動）" />;
}

// ---------------------------------------------------------------- simple horizontal bars

export function HBars({ items, theme: t, color }: { items: { label: string; value: number }[]; theme: Theme; color?: string }) {
  const option = useMemo<EChartsOption>(() => {
    const rows = [...items].reverse();
    return {
      animation: false,
      tooltip: { ...tooltip(t), trigger: "axis", axisPointer: { type: "shadow" } },
      grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
      xAxis: { type: "value", ...axisStyle(t), minInterval: 1 },
      yAxis: { type: "category", data: rows.map((r) => r.label), ...axisStyle(t), axisLabel: { color: t.text } },
      series: [{ type: "bar", data: rows.map((r) => r.value), itemStyle: { color: color ?? t.accent, borderRadius: [0, 4, 4, 0] }, barMaxWidth: 18 }],
    };
  }, [items, t, color]);
  return <EChart option={option} height={Math.max(120, items.length * 26 + 30)} />;
}

// ---------------------------------------------------------------- forest plot (one axis)

export function Forest({ points, axis, theme: t, onPick }: { points: SpectrumPoint[]; axis: Axis; theme: Theme; onPick?: (id: string) => void }) {
  const option = useMemo<EChartsOption>(() => {
    const rows = points.filter((p) => p.x != null).sort((a, b) => (b.x as number) - (a.x as number));
    const r = range(rows.map((p) => Math.abs(p.x as number) + (p.xci ?? 0)));
    return {
      animation: false,
      tooltip: {
        ...tooltip(t),
        formatter: (params: { data: { raw: SpectrumPoint } }) => {
          const p = params.data?.raw;
          return p ? `<b>${p.label}</b><br/>${AXIS_LABEL[axis]} ${signed(p.x)}${p.xci ? ` ± ${p.xci.toFixed(1)}` : ""}${p.n != null ? `<br/>${p.n} 篇` : ""}` : "";
        },
      },
      grid: { left: 8, right: 24, top: 10, bottom: 40, containLabel: true },
      xAxis: {
        type: "value", min: -r, max: r, name: `${AXIS_ENDS[axis][0]} ← ${AXIS_LABEL[axis]} → ${AXIS_ENDS[axis][1]}`, nameLocation: "middle", nameGap: 26,
        ...axisStyle(t), axisLabel: { color: t.muted, formatter: (v: number) => (v === 0 ? "0" : signed(v, 0)) },
      },
      yAxis: { type: "category", data: rows.map((p) => p.label), ...axisStyle(t), axisLabel: { color: t.text }, splitLine: { show: false } },
      series: [
        {
          type: "custom",
          silent: true,
          renderItem: (_: CustomSeriesRenderItemParams, api: CustomSeriesRenderItemAPI) => {
            const x = api.value(0) as number;
            const ci = api.value(2) as number;
            const y = api.value(1) as number;
            if (!ci) return { type: "group", children: [] };
            const a = api.coord([x - ci, y]);
            const b = api.coord([x + ci, y]);
            return { type: "line", shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, style: { stroke: t.muted, lineWidth: 1.5, opacity: 0.7 } };
          },
          data: rows.map((p, i) => [p.x, i, p.xci ?? 0]),
        },
        {
          type: "scatter",
          data: rows.map((p, i) => ({ value: [p.x, i], id: p.id, raw: p, itemStyle: { color: scoreColor(t, axis, p.x) } })),
          symbolSize: (_v: unknown, params: { data: { raw: SpectrumPoint } }) => {
            const maxN = Math.max(1, ...rows.map((p) => p.n ?? 1));
            return 7 + 13 * Math.sqrt((params.data.raw.n ?? 1) / maxN);
          },
          markLine: { silent: true, symbol: "none", lineStyle: { color: t.muted, type: "dashed", opacity: 0.5 }, label: { show: false }, data: [{ xAxis: 0 }] },
        },
      ],
    };
  }, [points, axis, t]);
  const n = points.filter((p) => p.x != null).length;
  return (
    <EChart
      option={option}
      height={Math.max(160, n * 28 + 60)}
      ariaLabel={`各媒體的${AXIS_LABEL[axis]}平均與信賴區間`}
      onClick={onPick ? (p) => { const d = p.data as { id?: string } | undefined; if (d?.id) onPick(d.id); } : undefined}
    />
  );
}
