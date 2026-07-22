"""个人复盘日记：账号隔离的记录、检索与交易上下文关联。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from sequoia_x.app.db import AppDatabase, utc_now


class JournalError(ValueError):
    """复盘日记输入或状态错误。"""


class JournalService:
    def __init__(self, db: AppDatabase) -> None:
        self.db = db

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        row["tags"] = json.loads(row.pop("tags_json") or "[]")
        row["related_symbols"] = json.loads(row.pop("related_symbols_json") or "[]")
        return row

    @staticmethod
    def _clean_list(values: list[str], *, symbols: bool = False) -> list[str]:
        result: list[str] = []
        for raw in values:
            value = raw.strip().upper() if symbols else raw.strip()
            if not value or value in result:
                continue
            if symbols and (len(value) != 6 or not value.isdigit()):
                raise JournalError(f"股票代码格式不正确：{raw}")
            if not symbols and len(value) > 20:
                raise JournalError("单个标签不能超过 20 个字符")
            result.append(value)
        return result

    def list_entries(
        self,
        owner_user_id: int,
        *,
        query: str = "",
        status: str = "",
        tag: str = "",
        start_date: str = "",
        end_date: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        clauses = ["owner_user_id=?"]
        params: list[Any] = [owner_user_id]
        if query.strip():
            pattern = f"%{query.strip()}%"
            clauses.append(
                "(title LIKE ? OR market_observation LIKE ? OR trade_review LIKE ? "
                "OR mistakes LIKE ? OR lessons LIKE ? OR tomorrow_plan LIKE ?)"
            )
            params.extend([pattern] * 6)
        if status:
            clauses.append("status=?")
            params.append(status)
        if tag.strip():
            clauses.append("EXISTS (SELECT 1 FROM json_each(tags_json) WHERE value=?)")
            params.append(tag.strip())
        if start_date:
            clauses.append("review_date>=?")
            params.append(start_date)
        if end_date:
            clauses.append("review_date<=?")
            params.append(end_date)
        where = " AND ".join(clauses)
        total = self.db.query_one(
            f"SELECT COUNT(*) count FROM personal_review_journal WHERE {where}", tuple(params)
        )
        rows = self.db.query_all(
            "SELECT * FROM personal_review_journal "
            f"WHERE {where} ORDER BY review_date DESC,id DESC LIMIT ? OFFSET ?",
            tuple([*params, page_size, (page - 1) * page_size]),
        )
        month_prefix = date.today().strftime("%Y-%m") + "%"
        stats = self.db.query_one(
            "SELECT COUNT(*) total,"
            "SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END) completed,"
            "SUM(CASE WHEN review_date LIKE ? THEN 1 ELSE 0 END) current_month,"
            "AVG(discipline_score) avg_discipline "
            "FROM personal_review_journal WHERE owner_user_id=?",
            (month_prefix, owner_user_id),
        ) or {}
        tags = self.db.query_all(
            "SELECT value tag,COUNT(*) count FROM personal_review_journal,"
            "json_each(tags_json) WHERE owner_user_id=? GROUP BY value "
            "ORDER BY count DESC,value LIMIT 30",
            (owner_user_id,),
        )
        return {
            "items": [self._decode(row) for row in rows],
            "total": int(total["count"] if total else 0),
            "page": page,
            "page_size": page_size,
            "stats": {
                "total": int(stats.get("total") or 0),
                "completed": int(stats.get("completed") or 0),
                "current_month": int(stats.get("current_month") or 0),
                "avg_discipline": float(stats["avg_discipline"] or 0),
            },
            "tags": tags,
        }

    def create(self, owner_user_id: int, payload: dict[str, Any]) -> int:
        tags = self._clean_list(payload.pop("tags"))
        symbols = self._clean_list(payload.pop("related_symbols"), symbols=True)
        now = utc_now()
        columns = [
            "review_date", "title", "status", "market_phase", "emotion",
            "discipline_score", "market_observation", "trade_review", "mistakes",
            "lessons", "tomorrow_plan",
        ]
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO personal_review_journal(owner_user_id,"
                    f"{','.join(columns)},tags_json,related_symbols_json,created_at,updated_at) "
                    f"VALUES ({','.join('?' for _ in range(len(columns) + 5))})",
                    (
                        owner_user_id,
                        *(payload[column] for column in columns),
                        json.dumps(tags, ensure_ascii=False),
                        json.dumps(symbols, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise JournalError("该日期已经有一篇复盘日记") from exc
            raise

    def update(self, entry_id: int, owner_user_id: int, payload: dict[str, Any]) -> None:
        tags = self._clean_list(payload.pop("tags"))
        symbols = self._clean_list(payload.pop("related_symbols"), symbols=True)
        columns = [
            "review_date", "title", "status", "market_phase", "emotion",
            "discipline_score", "market_observation", "trade_review", "mistakes",
            "lessons", "tomorrow_plan",
        ]
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    "UPDATE personal_review_journal SET "
                    + ",".join(f"{column}=?" for column in columns)
                    + ",tags_json=?,related_symbols_json=?,updated_at=? "
                    "WHERE id=? AND owner_user_id=?",
                    (
                        *(payload[column] for column in columns),
                        json.dumps(tags, ensure_ascii=False),
                        json.dumps(symbols, ensure_ascii=False),
                        utc_now(),
                        entry_id,
                        owner_user_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise JournalError("复盘日记不存在")
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise JournalError("该日期已经有一篇复盘日记") from exc
            raise

    def delete(self, entry_id: int, owner_user_id: int) -> None:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM personal_review_journal WHERE id=? AND owner_user_id=?",
                (entry_id, owner_user_id),
            )
            if cursor.rowcount == 0:
                raise JournalError("复盘日记不存在")

    def get(self, entry_id: int, owner_user_id: int) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM personal_review_journal WHERE id=? AND owner_user_id=?",
            (entry_id, owner_user_id),
        )
        if row is None:
            raise JournalError("复盘日记不存在")
        result = self._decode(row)
        result["context"] = self.context(owner_user_id, result["review_date"])
        return result

    def context(self, owner_user_id: int, review_date: str) -> dict[str, Any]:
        report = self.db.query_one(
            "SELECT id,title,summary_json FROM daily_report WHERE trade_date=?",
            (review_date,),
        )
        if report:
            report["summary"] = json.loads(report.pop("summary_json"))
        candidate = self.db.query_one(
            "SELECT COUNT(*) count,AVG(c.total_score) avg_score,"
            "SUM(CASE WHEN p.current_zone='LEFT' THEN 1 ELSE 0 END) left_count,"
            "SUM(CASE WHEN p.current_zone='MIDDLE' THEN 1 ELSE 0 END) middle_count,"
            "SUM(CASE WHEN p.current_zone='RIGHT' THEN 1 ELSE 0 END) right_count,"
            "SUM(CASE WHEN p.current_zone='VETO' THEN 1 ELSE 0 END) veto_count "
            "FROM candidate c JOIN trade_plan p ON p.candidate_id=c.id WHERE c.trade_date=?",
            (review_date,),
        ) or {}
        fills = self.db.query_all(
            "SELECT f.id,f.symbol,f.side,f.quantity,f.price,f.note,"
            "COALESCE(a.display_name,a.name) account_name "
            "FROM trade_fill f JOIN account a ON a.id=f.account_id "
            "LEFT JOIN trade_fill_reversal r ON r.fill_id=f.id "
            "WHERE a.owner_user_id=? AND f.trade_date=? AND r.id IS NULL ORDER BY f.id DESC",
            (owner_user_id, review_date),
        )
        snapshots = self.db.query_all(
            "SELECT s.account_id,COALESCE(a.display_name,a.name) account_name,s.cash,"
            "s.market_value,s.equity,s.unrealized_pnl,s.realized_pnl,s.drawdown,s.position_count "
            "FROM account_snapshot s JOIN account a ON a.id=s.account_id "
            "WHERE a.owner_user_id=? AND s.date=? ORDER BY s.account_id",
            (owner_user_id, review_date),
        )
        return {
            "report": report,
            "candidate": {
                "count": int(candidate.get("count") or 0),
                "avg_score": float(candidate.get("avg_score") or 0),
                "left_count": int(candidate.get("left_count") or 0),
                "middle_count": int(candidate.get("middle_count") or 0),
                "right_count": int(candidate.get("right_count") or 0),
                "veto_count": int(candidate.get("veto_count") or 0),
            },
            "fills": fills,
            "snapshots": snapshots,
        }
