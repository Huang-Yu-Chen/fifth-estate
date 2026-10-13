import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, CustomChart, HeatmapChart, LineChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  BarChart,
  CustomChart,
  HeatmapChart,
  LineChart,
  ScatterChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

export type EChartsOption = echarts.EChartsCoreOption;

interface Props {
  option: EChartsOption;
  height: number;
  minWidth?: number;
  onClick?: (params: { data?: unknown; name?: string; seriesIndex?: number; dataIndex?: number }) => void;
  ariaLabel?: string;
}

export default function EChart({ option, height, minWidth, onClick, ariaLabel }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = ref.current!;
    const c = echarts.init(el, undefined, { renderer: "canvas" });
    chart.current = c;
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      c.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, true);
  }, [option]);

  useEffect(() => {
    const c = chart.current;
    if (!c || !onClick) return;
    c.on("click", onClick as never);
    return () => {
      c.off("click");
    };
  }, [onClick]);

  const inner = <div ref={ref} role="img" aria-label={ariaLabel} style={{ width: "100%", height, minWidth }} />;
  return minWidth ? <div className="table-wrap">{inner}</div> : inner;
}
