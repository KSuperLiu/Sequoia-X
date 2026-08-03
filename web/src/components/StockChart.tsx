import * as echarts from "echarts";
import { useEffect, useRef, useState } from "react";

type Bar = { date: string; open: number; high: number; low: number; close: number; volume: number };
type TooltipParam = { axisValueLabel?: string; axisValue?: string; marker?: string; seriesName?: string; value?: unknown };

function ma(bars: Bar[], days: number) {
  return bars.map((_, index) => index + 1 < days ? "-" : +(bars.slice(index + 1 - days, index + 1).reduce((sum, bar) => sum + bar.close, 0) / days).toFixed(3));
}

export function stopLineLabel(stop: number, latestPrice?: number) {
  const price = latestPrice && latestPrice > 0 ? latestPrice : undefined;
  if (!price) return `止损 ${stop.toFixed(2)}`;
  const distance = (stop / price - 1) * 100;
  const sign = distance > 0 ? "+" : "";
  return `止损 ${stop.toFixed(2)} · 距最新价 ${sign}${distance.toFixed(1)}%`;
}

function escapeHtml(value: unknown) {
  return String(value ?? "—").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character] || character);
}

function stockTooltip(params: unknown) {
  const rows = (Array.isArray(params) ? params : [params]) as TooltipParam[];
  const candle = rows.find((row) => row.seriesName === "K线");
  const values = Array.isArray(candle?.value) ? candle.value : [];
  const axisValue = rows[0]?.axisValueLabel || rows[0]?.axisValue || "";
  const candleDetails = candle ? `<div>${candle.marker || ""}K线</div><div style="padding-left:14px">${[["开盘", values[0]], ["收盘", values[1]], ["最低", values[2]], ["最高", values[3]]].map(([label, value]) => `<div style="display:flex;justify-content:space-between;gap:18px"><span>${label}</span><b>${escapeHtml(value)}</b></div>`).join("")}</div>` : "";
  const indicators = rows.filter((row) => row.seriesName !== "K线" && row.seriesName !== "成交量").map((row) => `<div style="display:flex;justify-content:space-between;gap:18px">${row.marker || ""}<span>${escapeHtml(row.seriesName)}</span><b>${escapeHtml(row.value)}</b></div>`).join("");
  return `<div>${escapeHtml(axisValue)}</div>${candleDetails}${indicators}`;
}

export default function StockChart({ bars, entryLow, entryHigh, stop, latestPrice }: { bars: Bar[]; entryLow?: number; entryHigh?: number; stop?: number; latestPrice?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [stopHovered, setStopHovered] = useState(false);
  const [stopPinned, setStopPinned] = useState(false);
  const [stopLabelTop, setStopLabelTop] = useState<number>();
  const currentPrice = latestPrice && latestPrice > 0 ? latestPrice : bars[bars.length - 1]?.close;
  const stopText = stop ? stopLineLabel(stop, currentPrice) : "";
  useEffect(() => {
    if (!ref.current) return;
    setStopHovered(false);
    setStopPinned(false);
    const chart = echarts.init(ref.current);
    chart.setOption({ animation: false, tooltip: { trigger: "axis", formatter: stockTooltip }, legend: { data: ["K线", "MA10", "MA20", "MA60"] }, grid: [{ left: 55, right: 20, top: 45, height: "58%" }, { left: 55, right: 20, top: "76%", height: "14%" }], xAxis: [{ type: "category", data: bars.map((bar) => bar.date), boundaryGap: true, axisLine: { lineStyle: { color: "#9aa8bd" } } }, { type: "category", gridIndex: 1, data: bars.map((bar) => bar.date), axisLabel: { show: false } }], yAxis: [{ scale: true, splitLine: { lineStyle: { color: "#edf0f5" } } }, { gridIndex: 1, splitNumber: 2, axisLabel: { show: false } }], dataZoom: [{ type: "inside", start: Math.max(0, 100 - 12000 / Math.max(bars.length, 1)), end: 100 }, { show: true, bottom: 5, height: 20 }], series: [{ name: "K线", type: "candlestick", dimensions: ["开盘", "收盘", "最低", "最高"], data: bars.map((bar) => [bar.open, bar.close, bar.low, bar.high]), itemStyle: { color: "#e94d42", color0: "#22a96b", borderColor: "#e94d42", borderColor0: "#22a96b" }, markArea: entryLow && entryHigh ? { silent: true, itemStyle: { color: "rgba(238,126,27,.12)" }, data: [[{ yAxis: entryLow }, { yAxis: entryHigh }]] } : undefined, markLine: stop ? { symbol: "none", lineStyle: { color: "#e44343", type: "dashed", width: 1.5 }, label: { show: false }, data: [{ yAxis: stop }] } : undefined }, { name: "MA10", type: "line", data: ma(bars, 10), smooth: true, symbol: "none", lineStyle: { width: 1.2, color: "#ef8b2c" } }, { name: "MA20", type: "line", data: ma(bars, 20), smooth: true, symbol: "none", lineStyle: { width: 1.2, color: "#3868b2" } }, { name: "MA60", type: "line", data: ma(bars, 60), smooth: true, symbol: "none", lineStyle: { width: 1.2, color: "#8b62b0" } }, { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: bars.map((bar) => bar.volume), itemStyle: { color: "#8da4c6" } }] });
    const syncStopLabel = () => {
      if (!stop || !ref.current) return setStopLabelTop(undefined);
      const top = chart.convertToPixel({ yAxisIndex: 0 }, stop);
      setStopLabelTop(typeof top === "number" && Number.isFinite(top) ? Math.max(30, Math.min(top, ref.current.clientHeight - 10)) : undefined);
    };
    const showStop = (event: { componentType?: string }) => event.componentType === "markLine" && setStopHovered(true);
    const hideStop = (event: { componentType?: string }) => event.componentType === "markLine" && setStopHovered(false);
    const toggleStop = (event: { componentType?: string }) => event.componentType === "markLine" && setStopPinned((value) => !value);
    chart.on("mouseover", showStop);
    chart.on("mouseout", hideStop);
    chart.on("click", toggleStop);
    const frame = requestAnimationFrame(syncStopLabel);
    const resize = () => { chart.resize(); syncStopLabel(); };
    window.addEventListener("resize", resize);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
      chart.off("mouseover", showStop);
      chart.off("mouseout", hideStop);
      chart.off("click", toggleStop);
      chart.dispose();
    };
  }, [bars, entryLow, entryHigh, stop, stopText]);
  return <div className="stock-chart" role="img" aria-label={stopText ? `股票K线图，${stopText}` : "股票K线图"}>
    <div ref={ref} className="stock-chart-canvas" />
    {stopText && stopLabelTop !== undefined && (stopHovered || stopPinned) && <span className="stock-chart-stop-label" style={{ top: stopLabelTop }}>{stopText}</span>}
  </div>;
}
