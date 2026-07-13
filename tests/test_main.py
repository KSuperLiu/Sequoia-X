"""主程序入口属性测试。"""

import sys
from unittest.mock import patch

import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

# 预先导入 main 模块，避免在 @given 循环中重复导入
import main as main_module


class _FakeEngine:
    def __init__(self, settings: object) -> None:
        self.settings = settings
        self.backfill_symbols: list[str] | None = None

    def get_all_symbols(self) -> list[str]:
        return ["000001", "600000"]

    def backfill(self, symbols: list[str]) -> None:
        self.backfill_symbols = symbols


# Feature: sequoia-x-v2, Property 13: 主程序异常以非零退出码终止
@given(error_msg=st.text(min_size=1, max_size=100))
@h_settings(max_examples=30, deadline=None)
def test_main_exits_nonzero_on_exception(error_msg: str) -> None:
    """属性 13：main() 中任意未捕获异常应导致 sys.exit(1)。"""
    # patch main 模块中直接引用的 get_settings
    with patch.object(main_module, "get_settings", side_effect=RuntimeError(error_msg)):
        with pytest.raises(SystemExit) as exc_info:
            main_module.main()
        assert exc_info.value.code != 0


def test_backfill_uses_filtered_symbols_from_data_engine() -> None:
    """--backfill 应只回填 DataEngine.get_all_symbols() 返回的过滤后股票。"""
    fake_engine: _FakeEngine | None = None

    def make_engine(settings: object) -> _FakeEngine:
        nonlocal fake_engine
        fake_engine = _FakeEngine(settings)
        return fake_engine

    with (
        patch.object(sys, "argv", ["main.py", "--backfill"]),
        patch.object(main_module, "get_settings", return_value=object()),
        patch.object(main_module, "DataEngine", side_effect=make_engine),
    ):
        main_module.main()

    assert fake_engine is not None
    assert fake_engine.backfill_symbols == ["000001", "600000"]
