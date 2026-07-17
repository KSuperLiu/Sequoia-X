import * as echarts from "echarts";
import { useEffect, useRef } from "react";

export default function LineChart({ rows, benchmark = false }: { rows: Array<{ date: string; equity: number; benchmark?: number }>; benchmark?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (!ref.current) return; const chart = echarts.init(ref.current); chart.setOption({ animation: false, tooltip: { trigger: "axis" }, legend: { data: benchmark ? ["账户权益", "沪深300"] : ["账户权益"] }, grid: { left: 55, right: 24, top: 42, bottom: 35 }, xAxis: { type: "category", boundaryGap: false, data: rows.map((row) => row.date), axisLabel: { color: "#7a899f" } }, yAxis: { type: "value", scale: true, splitLine: { lineStyle: { color: "#edf1f6" } } }, dataZoom: rows.length > 80 ? [{ type: "inside", start: 50, end: 100 }] : undefined, series: [{ name: "账户权益", type: "line", smooth: true, symbol: "none", data: rows.map((row) => row.equity), lineStyle: { color: "#315da8", width: 2 }, areaStyle: { color: "rgba(49,93,168,.10)" } }, ...(benchmark ? [{ name: "沪深300", type: "line", smooth: true, symbol: "none", data: rows.map((row) => row.benchmark), lineStyle: { color: "#ee7e1b", width: 1.5 } }] : [])] }); const resize = () => chart.resize(); window.addEventListener("resize", resize); return () => { window.removeEventListener("resize", resize); chart.dispose(); }; }, [rows, benchmark]);
  return <div className="line-chart" ref={ref} />;
}
