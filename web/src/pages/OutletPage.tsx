import { useMemo } from "react";
import { useParams } from "react-router-dom";
import { useData, useMeta } from "../data";
import type { OutletDoc } from "../types";
import { signed, topicName } from "../format";
import { useTheme } from "../palette";
import EChart, { type EChartsOption } from "../components/EChart";
import { HBars } from "../components/Charts";
import ArticleList from "../components/ArticleList";
import { Empty, ErrorState, Loading } from "../components/States";

export default function OutletPage() {
  const { id = "" } = useParams<{ id: string }>();
  const theme = useTheme();
  const meta = useMeta();
  const doc = useData<OutletDoc>(`outlets/${id}.json`);

  const trend = useMemo<EChartsOption | null>(() => {
    if (!doc.data) return null;
    const wk = doc.data.weekly;
    return {
      animation: false,
      tooltip: { trigger: "axis", backgroundColor: theme.surface, borderColor: theme.line, textStyle: { color: theme.text, fontSize: 12 } },
      legend: { top: 0, textStyle: { color: theme.muted, fontSize: 12 }, data: ["綠藍平均", "抗中友中平均"] },
      grid: { left: 40, right: 12, top: 34, bottom: 28 },
      xAxis: { type: "category", data: wk.map((x) => x.week_end.slice(5).replace("-", "/")), axisLabel: { color: theme.muted }, axisLine: { lineStyle: { color: theme.line } } },
      yAxis: { type: "value", min: -5, max: 5, axisLabel: { color: theme.muted }, splitLine: { lineStyle: { color: theme.line, opacity: 0.6 } } },
      series: [
        { name: "綠藍平均", type: "line", data: wk.map((x) => x.lean), lineStyle: { width: 2.5, color: theme.buckets.lean[4] }, itemStyle: { color: theme.buckets.lean[4] }, connectNulls: false },
        { name: "抗中友中平均", type: "line", data: wk.map((x) => x.china), lineStyle: { width: 2.5, type: "dashed", color: theme.buckets.china[4] }, itemStyle: { color: theme.buckets.china[4] } },
      ],
    };
  }, [doc.data, theme]);

  if (meta.error) return <ErrorState error={meta.error} />;
  if (doc.error) return <Empty>找不到這家媒體。</Empty>;
  if (!meta.data || !doc.data) return <Loading />;
  const d = doc.data;
  const mix = Object.entries(d.topic_mix_30d).map(([t, n]) => ({ label: topicName(meta.data, t), value: n }));

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="crumbs">媒體</div>
          <h1>{d.name}</h1>
          <p>
            近 90 天平均：綠藍 {signed(d.baseline_lean)}、抗中友中 {signed(d.baseline_china)}。
            {d.via === "yahoo" && " 這家媒體的網站擋自動抓取，資料取自 Yahoo 奇摩新聞的授權轉載，只包含被轉載的文章。"}
          </p>
        </div>
      </div>
      <div className="grid-2">
        <section className="card">
          <h2>每週平均立場</h2>
          <div className="sub">−5 親綠／抗中 … +5 親藍／友中</div>
          {trend && <EChart option={trend} height={260} ariaLabel="每週平均綠藍與抗中友中立場" />}
        </section>
        <section className="card">
          <h2>議題分布（近 30 天）</h2>
          {mix.length ? <HBars items={mix} theme={theme} /> : <Empty>沒有資料。</Empty>}
        </section>
      </div>
      <section className="card">
        <h2>最新報導</h2>
        <ArticleList articles={d.articles} meta={meta.data} theme={theme} />
      </section>
    </div>
  );
}
