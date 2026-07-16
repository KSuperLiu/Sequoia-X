import * as echarts from "echarts";
import { useEffect, useRef } from "react";

type Bar = { date: string; open: number; high: number; low: number; close: number; volume: number };

function ma(bars: Bar[], days: number) {
  return bars.map((_, index) => index + 1 < days ? "-" : +(bars.slice(index + 1 - days, index + 1).reduce((sum, bar) => sum + bar.close, 0) / days).toFixed(3));
}

export default function StockChart({ bars, entryLow, entryHigh, stop }: { bars: Bar[]; entryLow?: number; entryHigh?: number; stop?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({ animation: false, tooltip: { trigger: "axis" }, legend: { data: ["K线", "MA10", "MA20", "MA60"] }, grid: [{ left: 55, right: 20, top: 45, height: "58%" }, { left: 55, right: 20, top: "76%", height: "14%" }], xAxis: [{ type: "category", data: bars.map((bar) => bar.date), boundaryGap: true, axisLine: { lineStyle: { color: "#9aa8bd" } } }, { type: "category", gridIndex: 1, data: bars.map((bar) => bar.date), axisLabel: { show: false } }], yAxis: [{ scale: true, splitLine: { lineStyle: { color: "#edf0f5" } } }, { gridIndex: 1, splitNumber: 2, axisLabel: { show: false } }], dataZoom: [{ type: "inside", start: Math.max(0, 100 - 12000 / Math.max(bars.length, 1)), end: 100 }, { show: true, bottom: 5, height: 20 }], series: [{ name: "K线", type: "candlestick", data: bars.map((bar) => [bar.open, bar.close, bar.low, bar.high]), itemStyle: { color: "#e94d42", color0: "#22a96b", borderColor: "#e94d42", borderColor0: "#22a96b" }, markArea: entryLow && entryHigh ? { silent: true, itemStyle: { color: "rgba(238,126,27,.12)" }, data: [[{ yAxis: entryLow }, { yAxis: entryHigh }]] } : undefined, markLine: stop ? { symbol: "none", lineStyle: { color: "#e44343", type: "dashed" }, label: { formatter: `止损 ${stop}` }, data: [{ yAxis: stop }] } : undefined }, { name: "MA10", type: "line", data: ma(bars, 10), smooth: true, symbol: "none", lineStyle: { width: 1.2, color: "#ef8b2c" } }, { name: "MA20", type: "line", data: ma(bars, 20), smooth: true, symbol: "none", lineStyle: { width: 1.2, color: "#3868b2" } }, { name: "MA60", type: "line", data: ma(bars, 60), smooth: true, symbol: "none", lineStyle: { width: 1.2, color: "#8b62b0" } }, { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: bars.map((bar) => bar.volume), itemStyle: { color: "#8da4c6" } }] });
    const resize = () => chart.resize(); window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.dispose(); };
  }, [bars, entryLow, entryHigh, stop]);
  return <div ref={ref} className="stock-chart" />;
}
