import { FormEvent, useState } from "react";
import { Calculator, Edit3 } from "lucide-react";
import { api, money, pct } from "../api";
import { Modal } from "./Ui";
import { useToast } from "./Toast";
import ValuationHistoryChart from "./ValuationHistoryChart";

export type ValuationData = {
  symbol: string;
  market_cap?: number;
  fundamental?: {
    report_period: string;
    announcement_date: string;
    revenue_ttm?: number;
    net_profit_ttm?: number;
    eps_ttm?: number;
    roe?: number;
    gross_margin?: number;
    net_margin?: number;
    yoy_net_profit?: number;
    liability_to_asset?: number;
    cfo_to_net_profit?: number;
    ps_ttm?: number;
    peg?: number;
    source: string;
  };
  history: Array<{ date: string; pe_ttm?: number; pb_mrq?: number }>;
  active_case?: {
    id: number;
    version: number;
    method: "PE" | "PB" | "PS";
    forecast_period: string;
    forecast_value: number;
    bear_multiple: number;
    base_multiple: number;
    bull_multiple: number;
    thesis: string;
    catalysts: string[];
    risks: string[];
    source_note: string;
    created_by: string;
    created_at: string;
  };
  result?: {
    as_of_date: string;
    current_price: number;
    bear_target: number;
    base_target: number;
    bull_target: number;
    margin_of_safety: number;
    valuation_zone: string;
    pe_percentile?: number;
    pb_percentile?: number;
    pe_sample_count: number;
    pb_sample_count: number;
  };
};

const zoneLabels: Record<string, string> = {
  UNDERVALUED: "低估",
  FAIR_LOW: "合理偏低",
  FAIR_HIGH: "合理偏高",
  OVERVALUED: "高估",
};

const methodUnits = { PE: "预测每股收益", PB: "预测每股净资产", PS: "预测每股营业收入" };

const billion = (value?: number) => value == null ? "—" : `${(value / 100_000_000).toFixed(2)} 亿`;
const multiple = (value?: number) => value == null ? "—" : `${value.toFixed(2)}x`;
const percentValue = (value?: number) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;

export default function ValuationPanel({
  symbol,
  data,
  isAdmin,
  onSaved,
}: {
  symbol: string;
  data: ValuationData;
  isAdmin: boolean;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const fundamental = data.fundamental;
  const result = data.result;
  const activeCase = data.active_case;

  return <>
    <section className="panel valuation-panel">
      <div className="panel-title">
        <div><Calculator size={17} /><h2>基本面估值</h2><small>独立于技术四维评分</small></div>
        <div className="valuation-actions">
          {result && <span className={`valuation-zone ${result.valuation_zone.toLowerCase()}`}>{zoneLabels[result.valuation_zone] || result.valuation_zone}</span>}
          {isAdmin && <button className="secondary icon-button" onClick={() => setEditing(true)}><Edit3 size={14} />{activeCase ? "修订估值" : "创建估值"}</button>}
        </div>
      </div>

      <div className="valuation-metrics">
        <div><span>总市值<small>公司全部股票的市场价值</small></span><b>{billion(data.market_cap)}</b></div>
        <div><span>P/S TTM<small>市值 ÷ 近12个月营业收入</small></span><b>{multiple(fundamental?.ps_ttm)}</b></div>
        <div><span>PEG<small>市盈率相对盈利增速</small></span><b>{multiple(fundamental?.peg)}</b></div>
        <div><span>ROE<small>净资产的赚钱效率</small></span><b>{percentValue(fundamental?.roe)}</b></div>
        <div><span>毛利率<small>每1元收入留下的毛利润</small></span><b>{percentValue(fundamental?.gross_margin)}</b></div>
        <div><span>净利同比<small>净利润较去年同期的增减</small></span><b className={(fundamental?.yoy_net_profit || 0) >= 0 ? "positive" : "negative"}>{percentValue(fundamental?.yoy_net_profit)}</b></div>
      </div>

      {fundamental ? <div className="valuation-freshness">
        财报期 {fundamental.report_period} · 公告日 {fundamental.announcement_date} · 数据源 {fundamental.source}
      </div> : <div className="warning-banner">暂无季度财务，请在任务中心执行“刷新季度财务”。</div>}

      {result && activeCase ? <>
        <div className="valuation-scenarios">
          <div className="bear"><span>悲观情景</span><strong>{money(result.bear_target)}</strong><small>{activeCase.bear_multiple}x</small></div>
          <div className="base"><span>基准合理价</span><strong>{money(result.base_target)}</strong><small>{activeCase.base_multiple}x · 安全边际 {pct(result.margin_of_safety)}</small></div>
          <div className="bull"><span>乐观情景</span><strong>{money(result.bull_target)}</strong><small>{activeCase.bull_multiple}x</small></div>
        </div>
        <div className="valuation-position">
          <i style={{ left: `${Math.max(0, Math.min(100, result.current_price / result.bull_target * 100))}%` }} />
          <span>当前价 {money(result.current_price)}</span><span>乐观价 {money(result.bull_target)}</span>
        </div>
        <div className="valuation-assumption">
          <b>{activeCase.method}估值 · {activeCase.forecast_period} · {methodUnits[activeCase.method]} {money(activeCase.forecast_value)}{activeCase.created_by === "system:auto" && <em className="auto-valuation-badge">系统参考</em>}</b>
          <p>{activeCase.thesis || "未填写核心假设。"}</p>
          <small>估值基准日 {result.as_of_date} · 版本 v{activeCase.version} · {activeCase.created_by === "system:auto" ? "系统自动生成" : activeCase.created_by} · {new Date(activeCase.created_at).toLocaleString("zh-CN")}</small>
        </div>
        {(activeCase.catalysts.length > 0 || activeCase.risks.length > 0) && <div className="valuation-notes">
          <div><b>催化剂</b>{activeCase.catalysts.length ? <ul>{activeCase.catalysts.map((item) => <li key={item}>{item}</li>)}</ul> : <p>—</p>}</div>
          <div><b>主要风险</b>{activeCase.risks.length ? <ul>{activeCase.risks.map((item) => <li key={item}>{item}</li>)}</ul> : <p>—</p>}</div>
        </div>}
      </> : <div className="valuation-empty">尚未建立三情景估值。系统会在季度财务刷新后为盈利且历史PE样本充足的股票自动生成参考估值。{isAdmin ? "也可以点击“创建估值”手动录入。" : ""}</div>}

      <div className="valuation-history-head">
        <div><b>历史估值位置</b><small>百分位越低，代表相对自身历史越便宜</small></div>
        <div><span>PE {result?.pe_percentile == null ? "—" : `${result.pe_percentile}%`}<small>{result?.pe_sample_count || 0}个样本</small></span><span>PB {result?.pb_percentile == null ? "—" : `${result.pb_percentile}%`}<small>{result?.pb_sample_count || 0}个样本</small></span></div>
      </div>
      {data.history.length > 1 ? <ValuationHistoryChart rows={data.history} /> : <div className="valuation-empty">历史样本不足，刷新任务完成后将显示近两年PE/PB曲线。</div>}
    </section>
    {editing && <ValuationCaseForm symbol={symbol} current={activeCase} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); onSaved(); }} />}
  </>;
}

function ValuationCaseForm({
  symbol,
  current,
  onClose,
  onSaved,
}: {
  symbol: string;
  current?: ValuationData["active_case"];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState({
    method: current?.method || "PE",
    forecast_period: current?.forecast_period || `${new Date().getFullYear() + 1}E`,
    forecast_value: current?.forecast_value || 0,
    bear_multiple: current?.bear_multiple || 0,
    base_multiple: current?.base_multiple || 0,
    bull_multiple: current?.bull_multiple || 0,
    thesis: current?.thesis || "",
    catalysts: current?.catalysts.join("\n") || "",
    risks: current?.risks.join("\n") || "",
    source_note: current?.source_note || "",
  });
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await api(`/stocks/${symbol}/valuation-cases`, {
        method: "POST",
        body: JSON.stringify({
          ...form,
          catalysts: form.catalysts.split("\n").map((item) => item.trim()).filter(Boolean),
          risks: form.risks.split("\n").map((item) => item.trim()).filter(Boolean),
        }),
      });
      toast.show(current ? "估值新版本已创建" : "估值方案已创建");
      onSaved();
    } catch (error) {
      toast.show((error as Error).message, "error");
    }
  };
  return <Modal title={`${current ? "修订" : "创建"} ${symbol} 三情景估值`} onClose={onClose} wide>
    <form className="stack-form" onSubmit={submit}>
      {current && <div className="warning-banner">保存后将生成v{current.version + 1}，历史版本仍保留。</div>}
      <div className="form-row">
        <label>估值方法<select value={form.method} onChange={(event) => setForm({ ...form, method: event.target.value as "PE" | "PB" | "PS" })}><option value="PE">PE 市盈率</option><option value="PB">PB 市净率</option><option value="PS">PS 市销率</option></select></label>
        <label>预测期<input value={form.forecast_period} onChange={(event) => setForm({ ...form, forecast_period: event.target.value })} /></label>
        <label>{methodUnits[form.method]}<input type="number" min="0.0001" step="0.0001" value={form.forecast_value} onChange={(event) => setForm({ ...form, forecast_value: +event.target.value })} /></label>
      </div>
      <div className="form-row">
        <label>悲观倍数<input type="number" min="0.01" step="0.01" value={form.bear_multiple} onChange={(event) => setForm({ ...form, bear_multiple: +event.target.value })} /></label>
        <label>基准倍数<input type="number" min="0.01" step="0.01" value={form.base_multiple} onChange={(event) => setForm({ ...form, base_multiple: +event.target.value })} /></label>
        <label>乐观倍数<input type="number" min="0.01" step="0.01" value={form.bull_multiple} onChange={(event) => setForm({ ...form, bull_multiple: +event.target.value })} /></label>
      </div>
      <label>核心假设<textarea value={form.thesis} onChange={(event) => setForm({ ...form, thesis: event.target.value })} /></label>
      <div className="form-row">
        <label>催化剂（每行一项）<textarea value={form.catalysts} onChange={(event) => setForm({ ...form, catalysts: event.target.value })} /></label>
        <label>主要风险（每行一项）<textarea value={form.risks} onChange={(event) => setForm({ ...form, risks: event.target.value })} /></label>
      </div>
      <label>预测来源与备注<textarea value={form.source_note} onChange={(event) => setForm({ ...form, source_note: event.target.value })} /></label>
      <button className="primary">保存为新版本</button>
    </form>
  </Modal>;
}
