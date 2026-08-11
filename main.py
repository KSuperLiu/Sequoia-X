"""Sequoia-X V2 主程序入口。

运行模式：
  python main.py                         # 日常行情、策略、追踪与飞书推送
  python main.py --backfill              # 回填本地市值门槛内股票的历史 K 线
  python main.py --refresh-market-cap    # 刷新本地市值表
  python main.py --refresh-financials    # 刷新跟踪股票季度财务与估值历史
"""

import argparse
import sys
from dotenv import load_dotenv
load_dotenv()

from datetime import date

import socket
socket.setdefaulttimeout(10.0)

from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.notify.feishu import FeishuNotifier
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.post_filter import StrategyPostFilter
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
from sequoia_x.strategy.private_placement import PrivatePlacementStrategy
from sequoia_x.app.db import AppDatabase
from sequoia_x.app.pipeline import DailyTrackingService
from sequoia_x.app.valuation import ValuationService


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequoia-X V2 选股系统")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="回填模式：按应用设置中的市值门槛拉取历史 K 线（默认不低于 50 亿）",
    )
    parser.add_argument(
        "--refresh-market-cap",
        action="store_true",
        help="联网刷新本地股票市值表，供后续采集离线过滤使用",
    )
    parser.add_argument(
        "--refresh-financials",
        action="store_true",
        help="刷新候选、自选和持仓股票的季度财务与近两年 PE/PB 历史",
    )
    args = parser.parse_args()

    try:
        # 1. 初始化配置
        settings = get_settings()

        # Web 设置页保存的股票池阈值同时作用于日常、回填与手动刷新入口。
        app_db = None
        app_db_path = getattr(settings, "app_db_path", None)
        if app_db_path:
            app_db = AppDatabase(app_db_path)
            configured_market_cap = app_db.get_setting("min_market_cap")
            if configured_market_cap is not None:
                object.__setattr__(settings, "min_market_cap", float(configured_market_cap))

        # 2. 初始化日志
        logger = get_logger(__name__)
        logger.info("Sequoia-X V2 启动")

        # 3. 初始化数据引擎
        engine = DataEngine(settings)

        if args.refresh_financials:
            app_db = app_db or AppDatabase(settings.app_db_path)
            result = ValuationService(app_db, engine).refresh_tracked()
            logger.info(
                "季度财务刷新完成：覆盖 %s 只，财报 %s 条，估值历史 %s 条，"
                "自动估值 %s 个，失败 %s 只",
                result["symbols"], result["fundamentals"], result["history_rows"],
                result["auto_cases"], result["failed"],
            )
            if result["failed"]:
                raise RuntimeError(f"季度财务刷新存在 {result['failed']} 只失败股票")
            return

        if args.refresh_market_cap:
            count = engine.refresh_market_cap_table()
            logger.info(f"本地股票市值表刷新完成，写入 {count} 只股票")
            return

        if args.backfill:
            # ── 回填模式：单线程保守拉历史 K 线，自动多轮重跑 ──
            logger.info("进入回填模式...")
            all_symbols = engine.get_all_symbols()
            engine.backfill(all_symbols)
            logger.info("Sequoia-X V2 回填模式运行完成")
            return

        # ── 日常模式：单次 API 补今天 + 策略 + 推送 ──
        logger.info("开始拉取最新快照...")
        count = engine.sync_today_bulk()
        logger.info(f"快照同步完成，写入 {count} 只股票")

        # 4. 策略列表（新增策略在此追加即可）
        strategies: list[BaseStrategy] = [
            MaVolumeStrategy(engine=engine, settings=settings),
            TurtleTradeStrategy(engine=engine, settings=settings),
            HighTightFlagStrategy(engine=engine, settings=settings),
            LimitUpShakeoutStrategy(engine=engine, settings=settings),
            RpsBreakoutStrategy(engine=engine, settings=settings),
            PrivatePlacementStrategy(engine=engine, settings=settings),
        ]

        notifier = FeishuNotifier(settings)

        # 5. 先收集所有策略原始结果，再统一做二次筛选，降低飞书噪音
        raw_results: dict[str, list[str]] = {}
        strategy_by_name: dict[str, BaseStrategy] = {}
        for strategy in strategies:
            strategy_name = type(strategy).__name__
            strategy_by_name[strategy_name] = strategy
            logger.info(f"执行策略：{strategy_name}")

            selected: list[str] = strategy.run()
            raw_results[strategy_name] = selected
            logger.info(f"{strategy_name} 原始选出 {len(selected)} 只股票")

        post_filter = StrategyPostFilter(engine)
        filtered_results = post_filter.filter_all(raw_results)

        # 6. 将策略结果固化为 Web 候选、交易计划和日报。
        # 追踪层故障不得破坏既有飞书选股链路。
        tracking_summary = None
        try:
            app_db = app_db or AppDatabase(settings.app_db_path)
            tracking = DailyTrackingService(app_db, engine)
            tracking_summary = tracking.persist(raw_results, filtered_results)
            logger.info(
                f"交易追踪日报生成完成：{tracking_summary['trade_date']}，"
                f"候选 {tracking_summary['candidate_count']} 只"
            )
        except Exception:
            logger.exception("交易追踪层执行失败，继续既有飞书推送")

        # 7. 有结果则推送至对应机器人
        for strategy_name, decision in filtered_results.items():
            strategy = strategy_by_name[strategy_name]
            selected = decision.selected
            raw_count = len(raw_results[strategy_name])
            logger.info(
                f"{strategy_name} 二次筛选：原始 {raw_count} 只，"
                f"成交额过滤 {decision.dropped_low_turnover} 只，"
                f"截断 {decision.truncated} 只，最终 {len(selected)} 只"
            )

            if selected:
                notifier.send(
                    symbols=selected,
                    strategy_name=strategy_name,
                    webhook_key=strategy.webhook_key,
                )
            else:
                logger.info(f"{strategy_name} 无选股结果，跳过推送")

        if tracking_summary:
            notifier.send_daily_summary(tracking_summary)

    except Exception:
        try:
            _logger = get_logger(__name__)
            _logger.exception("主流程发生未捕获异常，程序终止")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    logger.info("Sequoia-X V2 运行完成")


if __name__ == "__main__":
    main()
