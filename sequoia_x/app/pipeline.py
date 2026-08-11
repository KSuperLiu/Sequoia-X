"""把现有策略结果固化为可追踪候选、计划与 HTML 日报。"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sequoia_x.app.db import AppDatabase, utc_now
from sequoia_x.app.domain import PlanStatus, PositionZone, RunStatus
from sequoia_x.app.ledger import LedgerService
from sequoia_x.app.market import MarketEnricher
from sequoia_x.app.scoring import score_candidate
from sequoia_x.app.valuation import ValuationService
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine

logger = get_logger(__name__)


class DailyTrackingService:
    def __init__(self, app_db: AppDatabase, engine: DataEngine) -> None:
        self.app_db = app_db
        self.engine = engine
        self.market = MarketEnricher(app_db, engine)
        self.valuation = ValuationService(app_db, engine)

    def persist(
        self,
        raw_results: dict[str, list[str]],
        filtered_results: dict[str, Any],
    ) -> dict[str, Any]:
        trade_date = self.engine.get_latest_trade_date()
        if trade_date is None:
            raise RuntimeError("历史行情库为空，无法生成追踪结果")
        rule_id, rule = self.app_db.active_rule()
        now = utc_now()
        run_id = self._begin_run(trade_date, rule_id, now)

        filtered: dict[str, list[str]] = {
            name: list(decision.selected) for name, decision in filtered_results.items()
        }
        symbol_strategies: dict[str, list[str]] = defaultdict(list)
        for strategy_name, symbols in filtered.items():
            for symbol in symbols:
                symbol_strategies[symbol].append(strategy_name)

        symbols = sorted(symbol_strategies)
        tracked_rows = self.app_db.query_all(
            "SELECT DISTINCT symbol FROM trade_fill "
            "UNION SELECT DISTINCT symbol FROM candidate "
            "WHERE lifecycle_status IN ('WATCHING','PLANNED','BOUGHT') "
            "UNION SELECT symbol FROM watchlist_item"
        )
        tracked_symbols = {str(row["symbol"]) for row in tracked_rows}
        quote_symbols = sorted(set(symbols) | tracked_symbols)
        expected_date = self._expected_trade_date()
        calendar_fresh = expected_date is None or trade_date == expected_date
        quotes = self.market.fetch_quotes(quote_symbols, trade_date)
        # 行情快照已经落库后，按最新收盘价重算所有启用中的估值方案。
        self.valuation.refresh_active_results()
        self.market.refresh_profiles(quote_symbols)
        profiles = {
            row["symbol"]: row
            for row in self.app_db.query_all(
                "SELECT * FROM stock_profile WHERE symbol IN ({})".format(
                    ",".join("?" for _ in symbols) or "''"
                ),
                tuple(symbols),
            )
        }

        missing_quotes = sum(symbol not in quotes for symbol in quote_symbols)
        with self.app_db.transaction() as conn:
            for strategy_name, raw_symbols in raw_results.items():
                filtered_set = set(filtered.get(strategy_name, []))
                raw_set = set(raw_symbols)
                for symbol in sorted(raw_set | filtered_set):
                    conn.execute(
                        "INSERT INTO strategy_signal(run_id,trade_date,symbol,strategy_name,raw_selected,"
                        "filtered_selected,dropped_reason,created_at) VALUES (?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(run_id,symbol,strategy_name) DO UPDATE SET "
                        "raw_selected=excluded.raw_selected,filtered_selected=excluded.filtered_selected,"
                        "dropped_reason=excluded.dropped_reason",
                        (
                            run_id,
                            trade_date,
                            symbol,
                            strategy_name,
                            int(symbol in raw_set),
                            int(symbol in filtered_set),
                            None if symbol in filtered_set else "后处理过滤或超出推送上限",
                            now,
                        ),
                    )

            for symbol in symbols:
                quote = quotes.get(symbol)
                frame = self.engine.get_ohlcv(symbol, as_of_date=trade_date)
                try:
                    score = score_candidate(
                        frame, quote, len(symbol_strategies[symbol]), rule
                    )
                except ValueError as exc:
                    logger.warning(f"[{symbol}] 四维评分失败：{exc}")
                    continue
                profile = profiles.get(symbol, {})
                confidence = (
                    "HIGH"
                    if score.total_score >= 7 and len(symbol_strategies[symbol]) >= 2
                    else "MEDIUM" if score.total_score >= 5 else "LOW"
                )
                values = (
                    profile.get("name"), profile.get("industry"),
                    json.dumps(symbol_strategies[symbol], ensure_ascii=False),
                    len(symbol_strategies[symbol]), confidence, score.drawdown_60,
                    score.rebound_60, score.ma10, score.ma20, score.volume_ratio, score.atr14,
                    score.score_drawdown, score.score_rebound, score.score_ma, score.score_volume,
                    score.total_score, quote.get("close") if quote else None,
                    quote.get("pe_ttm") if quote else None, quote.get("pb_mrq") if quote else None,
                    score.entry_low, score.entry_high, score.stop_price, score.zone.value,
                    score.veto_reason, score.rationale,
                )
                existing = conn.execute(
                    "SELECT id FROM candidate WHERE run_id=? AND symbol=?", (run_id, symbol)
                ).fetchone()
                if existing:
                    candidate_id = int(existing["id"])
                    conn.execute(
                        "UPDATE candidate SET name=?,industry=?,strategies_json=?,consensus_count=?,"
                        "confidence=?,drawdown_60=?,rebound_60=?,ma10=?,ma20=?,volume_ratio=?,atr14=?,"
                        "score_drawdown=?,score_rebound=?,score_ma=?,score_volume=?,total_score=?,"
                        "real_close=?,pe_ttm=?,pb_mrq=?,entry_low=?,entry_high=?,stop_price=?,zone=?,"
                        "veto_reason=?,rationale=?,updated_at=? WHERE id=?",
                        (*values, now, candidate_id),
                    )
                else:
                    cursor = conn.execute(
                        "INSERT INTO candidate(run_id,trade_date,symbol,name,industry,strategies_json,"
                        "consensus_count,confidence,drawdown_60,rebound_60,ma10,ma20,volume_ratio,atr14,"
                        "score_drawdown,score_rebound,score_ma,score_volume,total_score,real_close,pe_ttm,"
                        "pb_mrq,entry_low,entry_high,stop_price,zone,veto_reason,rationale,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, trade_date, symbol, *values, now, now),
                    )
                    candidate_id = int(cursor.lastrowid)
                plan_status = (
                    PlanStatus.READY.value
                    if score.zone in {PositionZone.LEFT, PositionZone.MIDDLE}
                    else PlanStatus.DRAFT.value
                )
                conn.execute(
                    "INSERT INTO trade_plan(candidate_id,original_entry_low,original_entry_high,"
                    "original_stop_price,original_zone,current_entry_low,current_entry_high,"
                    "current_stop_price,current_zone,status,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(candidate_id) DO NOTHING",
                    (
                        candidate_id, score.entry_low, score.entry_high, score.stop_price,
                        score.zone.value, score.entry_low, score.entry_high, score.stop_price,
                        score.zone.value, plan_status, now, now,
                    ),
                )

        self.update_outcomes(trade_date)
        candidates = self.app_db.query_all(
            "SELECT c.*,p.id plan_id,p.status plan_status FROM candidate c "
            "JOIN trade_plan p ON p.candidate_id=c.id WHERE c.run_id=? "
            "ORDER BY c.total_score DESC,c.consensus_count DESC,c.symbol",
            (run_id,),
        )
        summary, report_html = self._build_report(trade_date, candidates)
        watch_alerts = self.app_db.query_all(
            "SELECT w.symbol,COALESCE(p.name,w.symbol) name,w.target_price,w.watch_price,s.close "
            "FROM watchlist_item w LEFT JOIN stock_profile p ON p.symbol=w.symbol "
            "JOIN market_snapshot s ON s.symbol=w.symbol AND s.date=? "
            "WHERE (w.target_price IS NOT NULL AND s.close>=w.target_price) "
            "OR (w.watch_price IS NOT NULL AND s.close<=w.watch_price)",
            (trade_date,),
        )
        summary["watchlist_alerts"] = watch_alerts
        with self.app_db.transaction() as conn:
            conn.execute("DELETE FROM daily_report WHERE run_id=?", (run_id,))
            conn.execute(
                "INSERT INTO daily_report(run_id,trade_date,title,summary_json,html,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    run_id,
                    trade_date,
                    f"Sequoia-X {trade_date} 日报",
                    json.dumps(summary, ensure_ascii=False),
                    report_html,
                    utc_now(),
                ),
            )
            data_fresh = calendar_fresh and missing_quotes == 0
            status = RunStatus.SUCCEEDED.value if data_fresh else RunStatus.PARTIAL.value
            issues: list[str] = []
            if not calendar_fresh:
                issues.append(f"行情最新日 {trade_date}，预期交易日 {expected_date}")
            if missing_quotes:
                issues.append(f"{missing_quotes} 只候选缺少真实行情")
            conn.execute(
                "UPDATE pipeline_run SET status=?,data_fresh=?,candidate_count=?,message=?,finished_at=? "
                "WHERE id=?",
                (
                    status,
                    int(data_fresh),
                    len(candidates),
                    "；".join(issues) if issues else "完成",
                    utc_now(),
                    run_id,
                ),
            )
        ledger = LedgerService(self.app_db)
        for account in self.app_db.query_all("SELECT id FROM account"):
            ledger.capture_snapshot(int(account["id"]), trade_date)
        return {"run_id": run_id, "trade_date": trade_date, "status": status, **summary}

    def _expected_trade_date(self, now: datetime | None = None) -> str | None:
        """返回当前时点应当已经完整入库的最近交易日。

        交易日的日跑默认在 18:30 执行，因此在配置的日跑时间之前，今天的
        日线尚不应被视为必需数据。这样盘中访问看板时，昨天的完整行情仍会
        被正确标记为新鲜。
        """
        current = now or datetime.now()
        today = current.date()
        schedule = (
            self.app_db.get_setting(
                "daily_run_time", self.engine.daily_run_time
            )
            or "18:30"
        )
        try:
            schedule_time = datetime.strptime(schedule, "%H:%M").time()
        except ValueError:
            schedule_time = datetime.strptime("18:30", "%H:%M").time()
        calendar_end = today if current.time() >= schedule_time else today - timedelta(days=1)
        start = calendar_end - timedelta(days=14)
        try:
            import baostock as bs

            login = bs.login()
            if login.error_code == "0":
                try:
                    result = bs.query_trade_dates(
                        start_date=start.isoformat(), end_date=calendar_end.isoformat()
                    )
                    open_days: list[str] = []
                    while result.error_code == "0" and result.next():
                        row = result.get_row_data()
                        if len(row) > 1 and row[1] == "1":
                            open_days.append(row[0])
                    if open_days:
                        return open_days[-1]
                finally:
                    bs.logout()
        except Exception:
            pass
        fallback = calendar_end
        while fallback.weekday() >= 5:
            fallback -= timedelta(days=1)
        return fallback.isoformat()

    def update_outcomes(self, latest_date: str) -> None:
        rows = self.app_db.query_all(
            "SELECT id,symbol,trade_date,real_close,entry_low,entry_high,stop_price "
            "FROM candidate WHERE trade_date<? AND trade_date>=date(?,'-60 day') "
            "AND return_20d IS NULL",
            (latest_date, latest_date),
        )
        for candidate in rows:
            frame = self.engine.get_ohlcv(candidate["symbol"])
            if frame.empty:
                continue
            signal_rows = frame[frame["date"] == candidate["trade_date"]]
            future = frame[frame["date"] > candidate["trade_date"]].head(20)
            if signal_rows.empty or future.empty:
                continue
            signal_close_hfq = float(signal_rows.iloc[-1]["close"])
            base = float(candidate["real_close"] or signal_close_hfq)
            factor = base / signal_close_hfq if signal_close_hfq > 0 else 1.0
            closes = future["close"].astype(float) * factor
            highs = future["high"].astype(float) * factor
            lows = future["low"].astype(float) * factor
            returns: dict[int, float | None] = {}
            for days in (1, 3, 5, 10, 20):
                returns[days] = float(closes.iloc[days - 1] / base - 1) if len(closes) >= days else None
            entry_low = candidate["entry_low"]
            entry_high = candidate["entry_high"]
            stop = candidate["stop_price"]
            hit_entry = bool(
                entry_low is not None and entry_high is not None
                and ((lows <= float(entry_high)) & (highs >= float(entry_low))).any()
            )
            hit_stop = bool(stop is not None and (lows <= float(stop)).any())
            with self.app_db.transaction() as conn:
                conn.execute(
                    "UPDATE candidate SET return_1d=?,return_3d=?,return_5d=?,return_10d=?,"
                    "return_20d=?,mfe=?,mae=?,hit_entry=?,hit_stop=?,"
                    "lifecycle_status=CASE WHEN ?>=20 AND lifecycle_status IN ('NEW','WATCHING') "
                    "THEN 'EXPIRED' ELSE lifecycle_status END,updated_at=? WHERE id=?",
                    (
                        returns[1], returns[3], returns[5], returns[10], returns[20],
                        float(highs.max() / base - 1), float(lows.min() / base - 1),
                        int(hit_entry), int(hit_stop), len(future), utc_now(), candidate["id"],
                    ),
                )

    def _begin_run(self, trade_date: str, rule_id: int, now: str) -> int:
        with self.app_db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM pipeline_run WHERE trade_date=? AND rule_version_id=?",
                (trade_date, rule_id),
            ).fetchone()
            if row:
                run_id = int(row["id"])
                conn.execute(
                    "UPDATE pipeline_run SET status='RUNNING',message=NULL,started_at=?,finished_at=NULL "
                    "WHERE id=?",
                    (now, run_id),
                )
                return run_id
            cursor = conn.execute(
                "INSERT INTO pipeline_run(trade_date,rule_version_id,status,started_at) VALUES (?,?,?,?)",
                (trade_date, rule_id, RunStatus.RUNNING.value, now),
            )
            return int(cursor.lastrowid)

    @staticmethod
    def _build_report(trade_date: str, candidates: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
        counts = {zone.value: 0 for zone in PositionZone}
        for candidate in candidates:
            counts[candidate["zone"]] = counts.get(candidate["zone"], 0) + 1
        eligible = [c for c in candidates if c["zone"] in {"LEFT", "MIDDLE"}]
        action = "不做任何买入" if not eligible else f"关注 {len(eligible)} 只可执行候选"
        summary = {
            "candidate_count": len(candidates),
            "zone_counts": counts,
            "eligible_count": len(eligible),
            "action": action,
        }
        rows = []
        for item in candidates:
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(item.get('name') or item['symbol']))}<small>{item['symbol']}</small></td>"
                f"<td>{html.escape(str(item.get('industry') or '-'))}</td>"
                f"<td>{item.get('real_close') or '-'}</td><td>{item.get('pe_ttm') or '-'}</td>"
                f"<td>{item['drawdown_60']:.1%}</td><td>{item['rebound_60']:.1%}</td>"
                f"<td><b>{item['total_score']}</b></td><td>{item['zone']}</td>"
                f"<td>{item.get('entry_low') or '-'} ~ {item.get('entry_high') or '-'}</td>"
                f"<td>{item.get('stop_price') or '-'}</td>"
                f"<td>{html.escape(item['rationale'])}</td></tr>"
            )
        generated = datetime.now().strftime("%Y-%m-%d %H:%M")
        report = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sequoia-X {trade_date} 日报</title>
<style>body{{font-family:system-ui;margin:0;background:#f3f6fb;color:#13233f}}header{{background:#294d91;color:white;padding:28px 5%}}
main{{padding:24px 5%}}.cards{{display:flex;gap:16px;flex-wrap:wrap}}.card{{background:white;border-radius:12px;padding:20px;min-width:150px;box-shadow:0 2px 10px #dce3ef}}
table{{width:100%;border-collapse:collapse;background:white;margin-top:24px;font-size:13px}}th,td{{padding:12px;border-bottom:1px solid #e5e9f0;text-align:left}}th{{background:#172944;color:white}}small{{display:block;color:#71809b}}</style></head>
<body><header><h1>Sequoia-X {trade_date} 日报</h1><p>生成时间 {generated} · 四维评分与风险计划</p></header><main>
<h2>核心结论：{action}</h2><div class="cards"><div class="card"><b>{len(candidates)}</b><br>覆盖标的</div>
<div class="card"><b>{counts['LEFT']}</b><br>左侧</div><div class="card"><b>{counts['MIDDLE']}</b><br>中部</div>
<div class="card"><b>{counts['RIGHT'] + counts['VETO']}</b><br>右侧/否决</div></div>
<table><thead><tr><th>标的</th><th>行业</th><th>现价</th><th>PE</th><th>回撤</th><th>反弹</th><th>总分</th><th>位置</th><th>买入区</th><th>止损</th><th>逻辑</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></main></body></html>"""
        return summary, report
