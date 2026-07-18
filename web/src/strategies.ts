export type StrategyMeta = {
  key: string;
  name: string;
  category: string;
  summary: string;
  conditions: string[];
  risk: string;
};

export const BACKTEST_STRATEGIES: StrategyMeta[] = [
  {
    key: "MaVolumeStrategy",
    name: "均线放量金叉",
    category: "趋势启动",
    summary: "寻找短期均线由弱转强、并得到成交量确认的启动信号。",
    conditions: ["昨日 MA5 低于 MA20，今日 MA5 上穿 MA20", "当日成交量大于 20 日均量的 1.5 倍"],
    risk: "震荡行情中容易反复金叉；放量只代表交易活跃，不保证趋势延续。",
  },
  {
    key: "TurtleTradeStrategy",
    name: "海龟突破（A股改良）",
    category: "趋势突破",
    summary: "寻找突破近 20 日高点、流动性充足且当日走势真实偏强的股票。",
    conditions: ["收盘价突破此前 20 个交易日最高价", "当日成交额超过 1 亿元", "收盘高于开盘且高于昨日收盘"],
    risk: "突破后仍可能快速回落，尤其要防范高开低走和假突破，需配合止损。",
  },
  {
    key: "HighTightFlagStrategy",
    name: "高位窄幅旗形",
    category: "强势整理",
    summary: "寻找大幅上涨后仍停留高位、波动明显收敛且成交缩量的强势整理形态。",
    conditions: ["40 日最高价 / 最低价大于 1.6", "近 10 日振幅小于 15%，且最低价不低于 40 日最高价的 80%", "当日成交量低于此前 20 日均量的 60%"],
    risk: "属于高位强势股形态，波动和回撤可能很大；缩量整理并不等于一定向上突破。",
  },
  {
    key: "LimitUpShakeoutStrategy",
    name: "涨停后放量洗盘",
    category: "事件形态",
    summary: "寻找涨停后放量收阴、但盘中低点仍守住涨停收盘价的洗盘形态。",
    conditions: ["昨日收盘较前日上涨至少 9.5%", "今日收阴，成交量超过昨日的 2 倍", "今日最低价不低于昨日收盘价"],
    risk: "事件驱动股票波动通常较大；一旦跌破昨日收盘支撑，原有洗盘假设就不再成立。",
  },
  {
    key: "RpsBreakoutStrategy",
    name: "RPS 强势临近新高",
    category: "相对强度",
    summary: "按 120 个交易日涨幅衡量全市场相对强度，筛选排名前列且接近阶段新高的股票。",
    conditions: ["具备完整 120 个交易日数据", "120 日涨幅百分位排名不低于 90", "最新收盘价不低于 120 日最高价的 90%"],
    risk: "依赖历史强势排名，市场风格切换时可能迅速失效，也存在追高风险。",
  },
];

const PRIVATE_PLACEMENT_STRATEGY: StrategyMeta = {
  key: "PrivatePlacementStrategy",
  name: "近期定向增发公告",
  category: "事件监控",
  summary: "监控最近一周内发生定向增发的 A 股公司，作为事件研究线索。",
  conditions: ["发行方式为定向增发，不包含公开增发", "发行日期位于最近 7 天内", "同一股票多条记录去重并优先展示近期事件"],
  risk: "公告本身不代表利好；需要进一步核对发行价格、股份稀释、锁定期、募资用途及数据源完整性。",
};

export const STRATEGIES: StrategyMeta[] = [...BACKTEST_STRATEGIES, PRIVATE_PLACEMENT_STRATEGY];

const RETIRED_STRATEGIES: StrategyMeta[] = [{
  key: "UptrendLimitDownStrategy",
  name: "上升趋势放量跌停（已下线）",
  category: "历史策略",
  summary: "曾用于寻找上升趋势中的放量跌停错杀机会，当前已停止生成新信号。",
  conditions: ["历史逻辑：MA20 高于 MA60", "历史逻辑：当日跌幅接近跌停且成交量超过 20 日均量的 2 倍"],
  risk: "该策略已经下线，仅用于解释历史候选记录，不能新建回测或产生新信号。",
}];

const STRATEGY_MAP = new Map([...STRATEGIES, ...RETIRED_STRATEGIES].map((item) => [item.key, item]));

export function strategyMeta(key: string): StrategyMeta {
  return STRATEGY_MAP.get(key) || {
    key,
    name: key,
    category: "其他策略",
    summary: "暂无策略说明。",
    conditions: [],
    risk: "请结合策略原始信号和风险规则审慎判断。",
  };
}

export function strategyLabel(key: string): string {
  return strategyMeta(key).name;
}
