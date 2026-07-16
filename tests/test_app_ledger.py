"""交易账本成本、现金、T+1 与模拟规则测试。"""

from pathlib import Path

import pytest

from sequoia_x.app.db import AppDatabase
from sequoia_x.app.ledger import LedgerError, LedgerService


def _service(tmp_path: Path) -> tuple[LedgerService, int]:
    service = LedgerService(AppDatabase(str(tmp_path / "app.db")))
    account_id = service.create_account(
        name="模拟组合",
        account_type="PAPER",
        initial_cash=100_000,
        commission_rate=0,
        minimum_commission=0,
        stamp_duty_rate=0,
        transfer_fee_rate=0,
    )
    return service, account_id


def test_weighted_average_cost_and_partial_sell(tmp_path: Path) -> None:
    service, account_id = _service(tmp_path)
    service.add_fill(
        account_id=account_id, symbol="600001", side="BUY", quantity=100,
        price=10, trade_date="2026-01-05",
    )
    service.add_fill(
        account_id=account_id, symbol="600001", side="BUY", quantity=100,
        price=20, trade_date="2026-01-06",
    )
    service.add_fill(
        account_id=account_id, symbol="600001", side="SELL", quantity=100,
        price=18, trade_date="2026-01-07",
    )
    position = service.positions(account_id)["600001"]
    assert position.quantity == 100
    assert position.average_cost == 15
    assert position.realized_pnl == 300
    assert service.cash_balance(account_id) == 98_800


def test_paper_account_enforces_lot_t1_and_cash(tmp_path: Path) -> None:
    service, account_id = _service(tmp_path)
    with pytest.raises(LedgerError, match="100 股"):
        service.add_fill(
            account_id=account_id, symbol="600001", side="BUY", quantity=50,
            price=10, trade_date="2026-01-05",
        )
    service.add_fill(
        account_id=account_id, symbol="600001", side="BUY", quantity=100,
        price=10, trade_date="2026-01-05",
    )
    with pytest.raises(LedgerError, match=r"T\+1"):
        service.add_fill(
            account_id=account_id, symbol="600001", side="SELL", quantity=100,
            price=11, trade_date="2026-01-05",
        )
    with pytest.raises(LedgerError, match="现金不足"):
        service.add_fill(
            account_id=account_id, symbol="600002", side="BUY", quantity=10_000,
            price=20, trade_date="2026-01-06",
        )
