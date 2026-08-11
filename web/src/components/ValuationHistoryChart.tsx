import * as echarts from "echarts";
import { useEffect, useRef } from "react";

type Row = { date: string; pe_ttm?: number; pb_mrq?: number };

export default function ValuationHistoryChart({ rows }: { rows: Row[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || rows.length === 0) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      animation: false,
      tooltip: { trigger: "axis" },
      legend: { data: ["PE TTM", "PB MRQ"], top: 0 },
      grid: { left: 48, right: 48, top: 42, bottom: 38 },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: rows.map((row) => row.date),
        axisLabel: { color: "#7a899f", formatter: (value: string) => value.slice(5) },
      },
      yAxis: [
        { type: "value", name: "PE", scale: true, splitLine: { lineStyle: { color: "#edf1f6" } } },
        { type: "value", name: "PB", scale: true, splitLine: { show: false } },
      ],
      dataZoom: rows.length > 120 ? [{ type: "inside", start: 50, end: 100 }] : undefined,
      series: [
        {
          name: "PE TTM",
          type: "line",
          symbol: "none",
          connectNulls: false,
          data: rows.map((row) => row.pe_ttm ?? null),
          lineStyle: { color: "#315da8", width: 1.8 },
        },
        {
          name: "PB MRQ",
          type: "line",
          yAxisIndex: 1,
          symbol: "none",
          connectNulls: false,
          data: rows.map((row) => row.pb_mrq ?? null),
          lineStyle: { color: "#ee7e1b", width: 1.5 },
        },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [rows]);

  return <div ref={ref} className="valuation-history-chart" aria-label="PE和PB历史估值曲线" />;
}
