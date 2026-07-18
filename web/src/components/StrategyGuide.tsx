import { useState } from "react";
import { BookOpenCheck, CircleAlert } from "lucide-react";
import { Modal } from "./Ui";
import { STRATEGIES, strategyMeta, type StrategyMeta } from "../strategies";

function StrategyCard({ strategy }: { strategy: StrategyMeta }) {
  return <article className="strategy-card"><div><span>{strategy.category}</span><code>{strategy.key}</code></div><h3>{strategy.name}</h3><p>{strategy.summary}</p><h4>触发条件</h4><ul>{strategy.conditions.map((condition) => <li key={condition}>{condition}</li>)}</ul><h4>主要风险</h4><p className="strategy-risk">{strategy.risk}</p></article>;
}

export function StrategyGuide({ strategyName }: { strategyName?: string }) {
  const strategies = strategyName ? [strategyMeta(strategyName)] : STRATEGIES;
  return <><div className="strategy-guide-note"><CircleAlert size={16} />策略只负责从市场中筛选形态，不等同于买入建议；候选还需经过评分、位置、数据新鲜度和账户风控判断。</div><div className={`strategy-card-grid ${strategyName ? "single" : ""}`}>{strategies.map((strategy) => <StrategyCard key={strategy.key} strategy={strategy} />)}</div></>;
}

export function StrategyHelpButton({ strategyName }: { strategyName?: string }) {
  const [open, setOpen] = useState(false);
  const strategy = strategyName ? strategyMeta(strategyName) : null;
  return <><button type="button" className="secondary icon-button strategy-help-button" onClick={() => setOpen(true)}><BookOpenCheck size={15} />{strategy ? "查看当前策略说明" : "策略说明"}</button>{open && <Modal title={strategy ? `${strategy.name} · 策略说明` : "选股策略说明"} onClose={() => setOpen(false)} wide><StrategyGuide strategyName={strategyName} /></Modal>}</>;
}
