"""季度财务采集、估值假设版本与可复算估值结果。"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from sequoia_x.app.db import AppDatabase, utc_now
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine

logger = get_logger(__name__)

ProgressCallback = Callable[[int, int, str], None]


def _session_expired(message: object) -> bool:
    text = str(message).lower()
    return "未登录" in text or "not login" in text


def _number(value: object) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        parsed = float(value)
        return parsed if parsed == parsed else None
    except (TypeError, ValueError):
        return None


def _quarter(report_period: str) -> int:
    month = int(report_period[5:7])
    return {3: 1, 6: 2, 9: 3, 12: 4}[month]


def _recent_quarters(today: date, count: int = 8) -> list[tuple[int, int]]:
    year = today.year
    quarter = (today.month - 1) // 3 + 1
    result: list[tuple[int, int]] = []
    for _ in range(count):
        result.append((year, quarter))
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
    return result


def percentile_rank(values: list[float], current: float | None) -> float | None:
    """返回当前值在有效历史样本中的百分位；数值越小，估值通常越便宜。"""
    valid = [float(value) for value in values if value is not None and float(value) > 0]
    if current is None or current <= 0 or not valid:
        return None
    return round(sum(value <= current for value in valid) / len(valid) * 100, 1)


def quantile(values: list[float], proportion: float) -> float | None:
    """使用线性插值计算分位数，忽略非正数。"""
    valid = sorted(float(value) for value in values if value is not None and float(value) > 0)
    if not valid:
        return None
    position = (len(valid) - 1) * proportion
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(valid[lower], 2)
    value = valid[lower] + (valid[upper] - valid[lower]) * (position - lower)
    return round(value, 2)


class BaostockFinancialProvider:
    """复用项目既有 Baostock 登录，获取季度指标和历史 PE/PB。"""

    def __init__(self, engine: DataEngine) -> None:
        self.engine = engine

    def open(self) -> Any:
        import baostock as bs

        if not self.engine._login_baostock(bs):
            raise RuntimeError("Baostock 登录失败")
        return bs

    @staticmethod
    def close(bs: Any) -> None:
        try:
            bs.logout()
        except Exception:
            pass

    def reopen(self, bs: Any) -> bool:
        """Baostock 长批次会话失效时重新登录。"""
        return bool(self.engine._login_baostock(bs))

    def quarterly(
        self, bs: Any, symbol: str, periods: list[tuple[int, int]]
    ) -> list[dict[str, Any]]:
        code = self.engine._to_baostock_code(symbol)
        endpoints = {
            "profit": bs.query_profit_data,
            "growth": bs.query_growth_data,
            "balance": bs.query_balance_data,
            "cash_flow": bs.query_cash_flow_data,
        }
        merged: dict[str, dict[str, Any]] = {}
        for year, quarter in periods:
            for endpoint, query in endpoints.items():
                result = query(code=code, year=year, quarter=quarter)
                if result.error_code != "0":
                    if _session_expired(result.error_msg):
                        raise RuntimeError(result.error_msg)
                    logger.warning(
                        "[%s] %s %sQ%s 获取失败：%s",
                        symbol,
                        endpoint,
                        year,
                        quarter,
                        result.error_msg,
                    )
                    continue
                while result.next():
                    row = dict(zip(result.fields, result.get_row_data()))
                    report_period = str(row.get("statDate") or "")
                    if not report_period:
                        continue
                    merged.setdefault(report_period, {})[endpoint] = row
        return [
            self._normalize(symbol, report_period, payload)
            for report_period, payload in sorted(merged.items())
            if payload.get("profit")
        ]

    @staticmethod
    def _normalize(
        symbol: str, report_period: str, payload: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        profit = payload.get("profit", {})
        growth = payload.get("growth", {})
        balance = payload.get("balance", {})
        cash_flow = payload.get("cash_flow", {})
        announcement_date = max(str(row.get("pubDate") or "") for row in payload.values())
        return {
            "symbol": symbol,
            "report_period": report_period,
            "announcement_date": announcement_date,
            "fiscal_year": int(report_period[:4]),
            "fiscal_quarter": _quarter(report_period),
            "revenue": _number(profit.get("MBRevenue")),
            "net_profit": _number(profit.get("netProfit")),
            "eps_ttm": _number(profit.get("epsTTM")),
            "total_shares": _number(profit.get("totalShare")),
            "roe": _number(profit.get("roeAvg")),
            "gross_margin": _number(profit.get("gpMargin")),
            "net_margin": _number(profit.get("npMargin")),
            "yoy_net_profit": _number(growth.get("YOYNI")),
            "yoy_eps": _number(growth.get("YOYEPSBasic")),
            "current_ratio": _number(balance.get("currentRatio")),
            "quick_ratio": _number(balance.get("quickRatio")),
            "cash_ratio": _number(balance.get("cashRatio")),
            "liability_to_asset": _number(balance.get("liabilityToAsset")),
            "cfo_to_revenue": _number(cash_flow.get("CFOToOR")),
            "cfo_to_net_profit": _number(cash_flow.get("CFOToNP")),
            "source": "baostock_quarterly",
            "source_payload_json": json.dumps(payload, ensure_ascii=False),
        }

    def valuation_history(
        self, bs: Any, symbol: str, start_date: str, end_date: str
    ) -> list[dict[str, Any]]:
        result = bs.query_history_k_data_plus(
            self.engine._to_baostock_code(symbol),
            "date,close,peTTM,pbMRQ",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="3",
        )
        if result.error_code != "0":
            raise RuntimeError(result.error_msg)
        rows: list[dict[str, Any]] = []
        while result.next():
            raw = dict(zip(result.fields, result.get_row_data()))
            rows.append(
                {
                    "date": raw.get("date"),
                    "close": _number(raw.get("close")),
                    "pe_ttm": _number(raw.get("peTTM")),
                    "pb_mrq": _number(raw.get("pbMRQ")),
                }
            )
        return rows


class ValuationService:
    def __init__(self, app_db: AppDatabase, engine: DataEngine) -> None:
        self.db = app_db
        self.engine = engine
        self.provider = BaostockFinancialProvider(engine)

    def tracked_symbols(self) -> list[str]:
        rows = self.db.query_all(
            "SELECT DISTINCT symbol FROM candidate "
            "WHERE trade_date=(SELECT MAX(trade_date) FROM candidate) "
            "UNION SELECT symbol FROM watchlist_item "
            "UNION SELECT DISTINCT symbol FROM trade_fill ORDER BY symbol"
        )
        return [str(row["symbol"]) for row in rows]

    def refresh_tracked(
        self,
        symbols: list[str] | None = None,
        progress: ProgressCallback | None = None,
        today: date | None = None,
    ) -> dict[str, int]:
        targets = sorted(set(symbols if symbols is not None else self.tracked_symbols()))
        if not targets:
            return {
                "symbols": 0,
                "fundamentals": 0,
                "history_rows": 0,
                "auto_cases": 0,
                "failed": 0,
            }
        current = today or date.today()
        periods = _recent_quarters(current)
        start_date = (current - timedelta(days=730)).isoformat()
        end_date = current.isoformat()
        fundamentals = history_rows = failed = 0
        bs = self.provider.open()
        try:
            for index, symbol in enumerate(targets, start=1):
                try:
                    try:
                        records = self.provider.quarterly(bs, symbol, periods)
                        history = self.provider.valuation_history(bs, symbol, start_date, end_date)
                    except Exception as exc:
                        if not _session_expired(exc):
                            raise
                        logger.info("[%s] Baostock 会话失效，重新登录后重试", symbol)
                        if not self.provider.reopen(bs):
                            raise RuntimeError("Baostock 重新登录失败") from exc
                        records = self.provider.quarterly(bs, symbol, periods)
                        history = self.provider.valuation_history(bs, symbol, start_date, end_date)
                    fundamentals += self._save_fundamentals(records)
                    history_rows += self._save_history(symbol, history)
                    self._rebuild_ttm(symbol)
                except Exception as exc:
                    failed += 1
                    logger.warning("[%s] 财务与估值历史刷新失败：%s", symbol, exc)
                message = f"财务数据 {index} / {len(targets)}：{symbol}"
                logger.info(message)
                if progress:
                    progress(index, len(targets), message)
        finally:
            self.provider.close(bs)
        auto_cases = self.refresh_reference_cases(targets)
        self.refresh_active_results()
        return {
            "symbols": len(targets),
            "fundamentals": fundamentals,
            "history_rows": history_rows,
            "auto_cases": auto_cases,
            "failed": failed,
        }

    def _save_fundamentals(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        now = utc_now()
        columns = (
            "symbol",
            "report_period",
            "announcement_date",
            "fiscal_year",
            "fiscal_quarter",
            "revenue",
            "net_profit",
            "eps_ttm",
            "total_shares",
            "roe",
            "gross_margin",
            "net_margin",
            "yoy_net_profit",
            "yoy_eps",
            "current_ratio",
            "quick_ratio",
            "cash_ratio",
            "liability_to_asset",
            "cfo_to_revenue",
            "cfo_to_net_profit",
            "source",
            "source_payload_json",
        )
        with self.db.transaction() as conn:
            for record in records:
                values = tuple(record.get(column) for column in columns)
                conn.execute(
                    f"INSERT INTO fundamental_snapshot({','.join(columns)},created_at,updated_at) "
                    f"VALUES ({','.join('?' for _ in columns)},?,?) "
                    "ON CONFLICT(symbol,report_period,announcement_date,source) DO UPDATE SET "
                    + ",".join(
                        f"{column}=excluded.{column}"
                        for column in columns
                        if column not in {"symbol", "report_period", "announcement_date", "source"}
                    )
                    + ",updated_at=excluded.updated_at",
                    (*values, now, now),
                )
        return len(records)

    def _save_history(self, symbol: str, rows: list[dict[str, Any]]) -> int:
        now = utc_now()
        with self.db.transaction() as conn:
            for row in rows:
                if not row.get("date"):
                    continue
                conn.execute(
                    "INSERT INTO market_snapshot("
                    "symbol,date,close,pe_ttm,pb_mrq,source,updated_at) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol,date) DO UPDATE SET "
                    "pe_ttm=COALESCE(excluded.pe_ttm,market_snapshot.pe_ttm),"
                    "pb_mrq=COALESCE(excluded.pb_mrq,market_snapshot.pb_mrq),"
                    "close=COALESCE(market_snapshot.close,excluded.close),updated_at=excluded.updated_at",
                    (
                        symbol,
                        row["date"],
                        row.get("close"),
                        row.get("pe_ttm"),
                        row.get("pb_mrq"),
                        "baostock_valuation_history",
                        now,
                    ),
                )
        return len(rows)

    def _rebuild_ttm(self, symbol: str) -> None:
        rows = self.db.query_all(
            "SELECT id,report_period,fiscal_quarter,revenue,net_profit FROM fundamental_snapshot "
            "WHERE symbol=? AND source='baostock_quarterly' "
            "ORDER BY report_period,announcement_date",
            (symbol,),
        )
        latest_by_period: dict[str, dict[str, Any]] = {}
        for row in rows:
            latest_by_period[str(row["report_period"])] = row
        with self.db.transaction() as conn:
            for period, row in latest_by_period.items():
                revenue_ttm = net_profit_ttm = None
                year = int(period[:4])
                quarter = int(row["fiscal_quarter"])
                if quarter == 4:
                    revenue_ttm = row.get("revenue")
                    net_profit_ttm = row.get("net_profit")
                else:
                    prior_annual = latest_by_period.get(f"{year - 1}-12-31")
                    prior_same = latest_by_period.get(
                        f"{year - 1}-{quarter * 3:02d}-{31 if quarter in {1, 4} else 30:02d}"
                    )
                    if prior_annual and prior_same:
                        if all(
                            value is not None
                            for value in (
                                row.get("revenue"),
                                prior_annual.get("revenue"),
                                prior_same.get("revenue"),
                            )
                        ):
                            revenue_ttm = (
                                float(prior_annual["revenue"])
                                - float(prior_same["revenue"])
                                + float(row["revenue"])
                            )
                        if all(
                            value is not None
                            for value in (
                                row.get("net_profit"),
                                prior_annual.get("net_profit"),
                                prior_same.get("net_profit"),
                            )
                        ):
                            net_profit_ttm = (
                                float(prior_annual["net_profit"])
                                - float(prior_same["net_profit"])
                                + float(row["net_profit"])
                            )
                conn.execute(
                    "UPDATE fundamental_snapshot SET revenue_ttm=?,net_profit_ttm=?,"
                    "updated_at=? WHERE id=?",
                    (revenue_ttm, net_profit_ttm, utc_now(), row["id"]),
                )

    def create_case(self, symbol: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        multiples = [
            float(payload[key]) for key in ("bear_multiple", "base_multiple", "bull_multiple")
        ]
        if not (0 < multiples[0] <= multiples[1] <= multiples[2]):
            raise ValueError("悲观、基准、乐观倍数必须为正数且依次递增")
        now = utc_now()
        with self.db.transaction() as conn:
            current = conn.execute(
                "SELECT COALESCE(MAX(version),0) version FROM valuation_case WHERE symbol=?",
                (symbol,),
            ).fetchone()
            version = int(current["version"]) + 1
            conn.execute("UPDATE valuation_case SET is_active=0 WHERE symbol=?", (symbol,))
            cursor = conn.execute(
                "INSERT INTO valuation_case(symbol,version,method,forecast_period,forecast_value,"
                "bear_multiple,base_multiple,bull_multiple,thesis,catalysts_json,risks_json,"
                "source_note,is_active,created_by,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (
                    symbol,
                    version,
                    payload["method"],
                    payload["forecast_period"],
                    float(payload["forecast_value"]),
                    *multiples,
                    payload.get("thesis", ""),
                    json.dumps(payload.get("catalysts", []), ensure_ascii=False),
                    json.dumps(payload.get("risks", []), ensure_ascii=False),
                    payload.get("source_note", ""),
                    actor,
                    now,
                ),
            )
            case_id = int(cursor.lastrowid)
        self._calculate_result(case_id)
        return self.detail(symbol)

    def refresh_reference_cases(self, symbols: list[str]) -> int:
        """为没有人工估值的盈利股票创建或更新系统 PE 参考方案。"""
        created = 0
        for symbol in symbols:
            payload = self._reference_case_payload(symbol)
            if payload is None:
                continue
            active = self.db.query_one(
                "SELECT * FROM valuation_case WHERE symbol=? AND is_active=1", (symbol,)
            )
            if active and active.get("created_by") != "system:auto":
                continue
            if active and all(
                active.get(key) == value
                for key, value in payload.items()
                if key
                in {
                    "method",
                    "forecast_period",
                    "forecast_value",
                    "bear_multiple",
                    "base_multiple",
                    "bull_multiple",
                    "source_note",
                }
            ):
                continue
            self.create_case(symbol, payload, "system:auto")
            created += 1
        return created

    def _reference_case_payload(self, symbol: str) -> dict[str, Any] | None:
        snapshot = self.db.query_one(
            "SELECT date,pe_ttm FROM market_snapshot WHERE symbol=? AND close>0 "
            "ORDER BY date DESC LIMIT 1",
            (symbol,),
        )
        if snapshot is None or not snapshot.get("pe_ttm") or float(snapshot["pe_ttm"]) <= 0:
            return None
        fundamental = self.db.query_one(
            "SELECT report_period,eps_ttm,yoy_net_profit FROM fundamental_snapshot "
            "WHERE symbol=? AND announcement_date<=? ORDER BY report_period DESC,"
            "announcement_date DESC LIMIT 1",
            (symbol, snapshot["date"]),
        )
        if fundamental is None or not fundamental.get("eps_ttm"):
            return None
        eps_ttm = float(fundamental["eps_ttm"])
        if eps_ttm <= 0:
            return None
        history = self.db.query_all(
            "SELECT pe_ttm FROM market_snapshot WHERE symbol=? AND date<=? AND pe_ttm>0 "
            "ORDER BY date DESC LIMIT 520",
            (symbol, snapshot["date"]),
        )
        pe_values = [float(row["pe_ttm"]) for row in history]
        if len(pe_values) < 60:
            return None
        multiples = [quantile(pe_values, point) for point in (0.25, 0.5, 0.75)]
        if any(value is None for value in multiples):
            return None
        growth = _number(fundamental.get("yoy_net_profit")) or 0.0
        applied_growth = max(-0.3, min(0.5, growth))
        forecast = round(eps_ttm * (1 + applied_growth), 4)
        if forecast <= 0:
            return None
        report_period = str(fundamental["report_period"])
        return {
            "method": "PE",
            "forecast_period": f"{int(str(snapshot['date'])[:4]) + 1}E",
            "forecast_value": forecast,
            "bear_multiple": multiples[0],
            "base_multiple": multiples[1],
            "bull_multiple": multiples[2],
            "thesis": (
                f"系统参考：最新 EPS TTM 为 {eps_ttm:.4f} 元，净利润同比增速 "
                f"{growth * 100:.1f}%，估算时采用 {applied_growth * 100:.1f}% 的限幅增速。"
            ),
            "catalysts": [],
            "risks": ["历史估值分位不代表未来，业绩预测可能偏离实际。"],
            "source_note": (
                f"Baostock 财报期 {report_period}；近两年 PE 有效样本 {len(pe_values)} 个"
            ),
        }

    def refresh_active_results(self) -> None:
        for row in self.db.query_all("SELECT id FROM valuation_case WHERE is_active=1"):
            self._calculate_result(int(row["id"]))

    def _calculate_result(self, case_id: int) -> dict[str, Any] | None:
        case = self.db.query_one("SELECT * FROM valuation_case WHERE id=?", (case_id,))
        if case is None:
            return None
        snapshot = self.db.query_one(
            "SELECT date,close,pe_ttm,pb_mrq FROM market_snapshot WHERE symbol=? "
            "AND close>0 ORDER BY date DESC LIMIT 1",
            (case["symbol"],),
        )
        if snapshot is None:
            return None
        history = self.db.query_all(
            "SELECT pe_ttm,pb_mrq FROM market_snapshot WHERE symbol=? AND date<=? ORDER BY date",
            (case["symbol"], snapshot["date"]),
        )
        pe_values = [
            float(row["pe_ttm"]) for row in history if row.get("pe_ttm") and row["pe_ttm"] > 0
        ]
        pb_values = [
            float(row["pb_mrq"]) for row in history if row.get("pb_mrq") and row["pb_mrq"] > 0
        ]
        current_price = float(snapshot["close"])
        forecast = float(case["forecast_value"])
        targets = [
            round(forecast * float(case[key]), 2)
            for key in ("bear_multiple", "base_multiple", "bull_multiple")
        ]
        zone = (
            "UNDERVALUED"
            if current_price <= targets[0]
            else "FAIR_LOW"
            if current_price <= targets[1]
            else "FAIR_HIGH"
            if current_price <= targets[2]
            else "OVERVALUED"
        )
        margin = (targets[1] - current_price) / targets[1]
        now = utc_now()
        values = (
            case_id,
            snapshot["date"],
            current_price,
            *targets,
            margin,
            zone,
            percentile_rank(pe_values, _number(snapshot.get("pe_ttm"))),
            percentile_rank(pb_values, _number(snapshot.get("pb_mrq"))),
            len(pe_values),
            len(pb_values),
            now,
        )
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO valuation_result("
                "valuation_case_id,as_of_date,current_price,bear_target,"
                "base_target,bull_target,margin_of_safety,valuation_zone,pe_percentile,pb_percentile,"
                "pe_sample_count,pb_sample_count,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(valuation_case_id,as_of_date) DO UPDATE SET "
                "current_price=excluded.current_price,bear_target=excluded.bear_target,"
                "base_target=excluded.base_target,bull_target=excluded.bull_target,"
                "margin_of_safety=excluded.margin_of_safety,valuation_zone=excluded.valuation_zone,"
                "pe_percentile=excluded.pe_percentile,pb_percentile=excluded.pb_percentile,"
                "pe_sample_count=excluded.pe_sample_count,pb_sample_count=excluded.pb_sample_count,"
                "created_at=excluded.created_at",
                values,
            )
        return self.db.query_one(
            "SELECT * FROM valuation_result WHERE valuation_case_id=? AND as_of_date=?",
            (case_id, snapshot["date"]),
        )

    def detail(self, symbol: str) -> dict[str, Any]:
        snapshot = self.db.query_one(
            "SELECT * FROM market_snapshot WHERE symbol=? ORDER BY date DESC LIMIT 1", (symbol,)
        )
        as_of_date = str(snapshot["date"]) if snapshot else date.today().isoformat()
        fundamental = self.db.query_one(
            "SELECT * FROM fundamental_snapshot WHERE symbol=? AND announcement_date<=? "
            "ORDER BY report_period DESC,announcement_date DESC LIMIT 1",
            (symbol, as_of_date),
        )
        market_cap = self._market_cap(symbol)
        if fundamental:
            revenue_ttm = fundamental.get("revenue_ttm")
            fundamental["ps_ttm"] = (
                market_cap / float(revenue_ttm)
                if market_cap and revenue_ttm and float(revenue_ttm) > 0
                else None
            )
            growth = fundamental.get("yoy_net_profit")
            pe = snapshot.get("pe_ttm") if snapshot else None
            fundamental["peg"] = (
                float(pe) / (float(growth) * 100)
                if pe and growth and float(pe) > 0 and float(growth) > 0
                else None
            )
            fundamental.pop("source_payload_json", None)
        history = self.db.query_all(
            "SELECT date,pe_ttm,pb_mrq FROM market_snapshot WHERE symbol=? "
            "AND date<=? AND (pe_ttm>0 OR pb_mrq>0) ORDER BY date DESC LIMIT 520",
            (symbol, as_of_date),
        )
        history.reverse()
        case = self.db.query_one(
            "SELECT * FROM valuation_case WHERE symbol=? AND is_active=1", (symbol,)
        )
        result = None
        if case:
            case["catalysts"] = json.loads(case.pop("catalysts_json"))
            case["risks"] = json.loads(case.pop("risks_json"))
            result = self.db.query_one(
                "SELECT * FROM valuation_result WHERE valuation_case_id=? "
                "ORDER BY as_of_date DESC LIMIT 1",
                (case["id"],),
            )
        return {
            "symbol": symbol,
            "market_cap": market_cap,
            "fundamental": fundamental,
            "history": history,
            "active_case": case,
            "result": result,
        }

    def _market_cap(self, symbol: str) -> float | None:
        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                row = conn.execute(
                    "SELECT market_cap FROM stock_market_cap WHERE symbol=?", (symbol,)
                ).fetchone()
            return float(row[0]) if row and row[0] is not None else None
        except sqlite3.Error:
            return None
