"""自定义策略注册模块单元测试。

覆盖：
- StrategyRegistry 注册、查询、列表功能
- 通过 entry_points 加载策略
- 通过目录扫描加载策略
- 校验：非 BaseStrategy 子类拒绝注册
- 全局注册表 get_strategy_registry / reset_global_registry

需求: 5.9, 10.5
"""

from __future__ import annotations

import textwrap
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from app.domain.strategy.base import BaseStrategy, StrategyParams
from app.domain.strategy.registry import (
    STRATEGY_ENTRY_POINT_GROUP,
    StrategyRegistry,
    get_strategy_registry,
    reset_global_registry,
)


# ---------------------------------------------------------------------------
# 测试用策略类
# ---------------------------------------------------------------------------


class AlphaStrategy(BaseStrategy):
    """测试策略 A。"""

    name = "alpha"

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        return []


class BetaStrategy(BaseStrategy):
    """测试策略 B。"""

    name = "beta"

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        return []


class NoNameStrategy(BaseStrategy):
    """没有定义 name 的策略。"""

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        return []


class UnnamedStrategy(BaseStrategy):
    """使用默认 name 的策略。"""

    name = "unnamed_strategy"

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        return []


# ---------------------------------------------------------------------------
# StrategyRegistry 基本功能测试
# ---------------------------------------------------------------------------


class TestStrategyRegistryBasic:
    """StrategyRegistry 基本注册与查询测试。"""

    def test_register_and_get(self) -> None:
        """注册策略后可以通过名称获取。"""
        registry = StrategyRegistry()
        registry.register(AlphaStrategy)

        result = registry.get("alpha")
        assert result is AlphaStrategy

    def test_register_multiple(self) -> None:
        """注册多个策略。"""
        registry = StrategyRegistry()
        registry.register(AlphaStrategy)
        registry.register(BetaStrategy)

        assert registry.count == 2
        assert registry.get("alpha") is AlphaStrategy
        assert registry.get("beta") is BetaStrategy

    def test_get_nonexistent_returns_none(self) -> None:
        """查询不存在的策略返回 None。"""
        registry = StrategyRegistry()
        assert registry.get("nonexistent") is None

    def test_list_strategies(self) -> None:
        """列出所有已注册策略。"""
        registry = StrategyRegistry()
        registry.register(AlphaStrategy)
        registry.register(BetaStrategy)

        strategies = registry.list_strategies()
        assert "alpha" in strategies
        assert "beta" in strategies
        assert strategies["alpha"] is AlphaStrategy

    def test_list_names(self) -> None:
        """列出所有策略名称（已排序）。"""
        registry = StrategyRegistry()
        registry.register(BetaStrategy)
        registry.register(AlphaStrategy)

        names = registry.list_names()
        assert names == ["alpha", "beta"]

    def test_count(self) -> None:
        """策略计数。"""
        registry = StrategyRegistry()
        assert registry.count == 0

        registry.register(AlphaStrategy)
        assert registry.count == 1

    def test_unregister(self) -> None:
        """取消注册策略。"""
        registry = StrategyRegistry()
        registry.register(AlphaStrategy)

        assert registry.unregister("alpha") is True
        assert registry.get("alpha") is None
        assert registry.count == 0

    def test_unregister_nonexistent(self) -> None:
        """取消注册不存在的策略返回 False。"""
        registry = StrategyRegistry()
        assert registry.unregister("nonexistent") is False

    def test_clear(self) -> None:
        """清空注册表。"""
        registry = StrategyRegistry()
        registry.register(AlphaStrategy)
        registry.register(BetaStrategy)

        registry.clear()
        assert registry.count == 0

    def test_duplicate_name_overwrites(self) -> None:
        """重复名称的策略会覆盖之前的注册。"""

        class AlphaV2(BaseStrategy):
            name = "alpha"

            def on_bar(self, context: BarContext) -> list[OrderIntent]:
                return []

        # 需要设置 __module__ 以避免被 _register_from_module 过滤
        AlphaV2.__module__ = __name__

        registry = StrategyRegistry()
        registry.register(AlphaStrategy)
        registry.register(AlphaV2)

        assert registry.get("alpha") is AlphaV2
        assert registry.count == 1


# ---------------------------------------------------------------------------
# 注册校验测试
# ---------------------------------------------------------------------------


class TestStrategyRegistryValidation:
    """策略注册校验测试。"""

    def test_reject_non_class(self) -> None:
        """拒绝非类对象。"""
        registry = StrategyRegistry()
        with pytest.raises(TypeError, match="必须继承 BaseStrategy"):
            registry.register("not_a_class")  # type: ignore

    def test_reject_non_basestrategy_subclass(self) -> None:
        """拒绝非 BaseStrategy 子类。"""
        registry = StrategyRegistry()

        class NotAStrategy:
            name = "fake"

        with pytest.raises(TypeError, match="必须继承 BaseStrategy"):
            registry.register(NotAStrategy)  # type: ignore

    def test_reject_basestrategy_itself(self) -> None:
        """拒绝注册 BaseStrategy 本身。"""
        registry = StrategyRegistry()
        with pytest.raises(TypeError, match="不能注册 BaseStrategy 抽象类本身"):
            registry.register(BaseStrategy)

    def test_reject_no_name(self) -> None:
        """拒绝没有 name 属性的策略。"""
        registry = StrategyRegistry()
        with pytest.raises(ValueError, match="必须定义非空的 name 属性"):
            registry.register(NoNameStrategy)

    def test_reject_unnamed_strategy(self) -> None:
        """拒绝使用默认 unnamed_strategy 名称的策略。"""
        registry = StrategyRegistry()
        with pytest.raises(ValueError, match="必须定义非空的 name 属性"):
            registry.register(UnnamedStrategy)


# ---------------------------------------------------------------------------
# 目录扫描加载测试
# ---------------------------------------------------------------------------


class TestLoadFromDirectory:
    """从目录加载策略测试。"""

    def test_load_valid_strategy_file(self, tmp_path: Path) -> None:
        """从目录加载有效的策略文件。"""
        strategy_file = tmp_path / "my_strategy.py"
        strategy_file.write_text(
            textwrap.dedent("""\
                from app.domain.strategy.base import BaseStrategy
                from app.domain.backtest.order import OrderIntent
                from app.domain.backtest.engine_event import BarContext

                class MyCustomStrategy(BaseStrategy):
                    name = "my_custom"

                    def on_bar(self, context: BarContext) -> list[OrderIntent]:
                        return []
            """),
            encoding="utf-8",
        )

        registry = StrategyRegistry()
        count = registry.load_from_directory(tmp_path)

        assert count == 1
        assert registry.get("my_custom") is not None
        assert registry.get("my_custom").__name__ == "MyCustomStrategy"

    def test_load_multiple_strategies_from_file(self, tmp_path: Path) -> None:
        """单个文件中包含多个策略。"""
        strategy_file = tmp_path / "multi.py"
        strategy_file.write_text(
            textwrap.dedent("""\
                from app.domain.strategy.base import BaseStrategy
                from app.domain.backtest.order import OrderIntent
                from app.domain.backtest.engine_event import BarContext

                class StrategyOne(BaseStrategy):
                    name = "strat_one"

                    def on_bar(self, context: BarContext) -> list[OrderIntent]:
                        return []

                class StrategyTwo(BaseStrategy):
                    name = "strat_two"

                    def on_bar(self, context: BarContext) -> list[OrderIntent]:
                        return []
            """),
            encoding="utf-8",
        )

        registry = StrategyRegistry()
        count = registry.load_from_directory(tmp_path)

        assert count == 2
        assert registry.get("strat_one") is not None
        assert registry.get("strat_two") is not None

    def test_skip_private_files(self, tmp_path: Path) -> None:
        """跳过以 _ 开头的文件。"""
        private_file = tmp_path / "_helper.py"
        private_file.write_text(
            textwrap.dedent("""\
                from app.domain.strategy.base import BaseStrategy
                from app.domain.backtest.order import OrderIntent
                from app.domain.backtest.engine_event import BarContext

                class HiddenStrategy(BaseStrategy):
                    name = "hidden"

                    def on_bar(self, context: BarContext) -> list[OrderIntent]:
                        return []
            """),
            encoding="utf-8",
        )

        registry = StrategyRegistry()
        count = registry.load_from_directory(tmp_path)

        assert count == 0
        assert registry.get("hidden") is None

    def test_skip_init_file(self, tmp_path: Path) -> None:
        """跳过 __init__.py。"""
        init_file = tmp_path / "__init__.py"
        init_file.write_text(
            textwrap.dedent("""\
                from app.domain.strategy.base import BaseStrategy
                from app.domain.backtest.order import OrderIntent
                from app.domain.backtest.engine_event import BarContext

                class InitStrategy(BaseStrategy):
                    name = "init_strat"

                    def on_bar(self, context: BarContext) -> list[OrderIntent]:
                        return []
            """),
            encoding="utf-8",
        )

        registry = StrategyRegistry()
        count = registry.load_from_directory(tmp_path)

        assert count == 0

    def test_skip_non_python_files(self, tmp_path: Path) -> None:
        """跳过非 .py 文件。"""
        txt_file = tmp_path / "readme.txt"
        txt_file.write_text("This is not a strategy file.")

        registry = StrategyRegistry()
        count = registry.load_from_directory(tmp_path)

        assert count == 0

    def test_invalid_file_does_not_block_others(self, tmp_path: Path) -> None:
        """无效文件不阻塞其他策略加载。"""
        # 有语法错误的文件
        bad_file = tmp_path / "bad_strategy.py"
        bad_file.write_text("this is not valid python !!!", encoding="utf-8")

        # 有效的策略文件
        good_file = tmp_path / "good_strategy.py"
        good_file.write_text(
            textwrap.dedent("""\
                from app.domain.strategy.base import BaseStrategy
                from app.domain.backtest.order import OrderIntent
                from app.domain.backtest.engine_event import BarContext

                class GoodStrategy(BaseStrategy):
                    name = "good"

                    def on_bar(self, context: BarContext) -> list[OrderIntent]:
                        return []
            """),
            encoding="utf-8",
        )

        registry = StrategyRegistry()
        count = registry.load_from_directory(tmp_path)

        assert count == 1
        assert registry.get("good") is not None

    def test_directory_not_found(self) -> None:
        """目录不存在时抛出 FileNotFoundError。"""
        registry = StrategyRegistry()
        with pytest.raises(FileNotFoundError, match="策略目录不存在"):
            registry.load_from_directory("/nonexistent/path")

    def test_not_a_directory(self, tmp_path: Path) -> None:
        """路径不是目录时抛出 NotADirectoryError。"""
        file_path = tmp_path / "not_a_dir.txt"
        file_path.write_text("hello")

        registry = StrategyRegistry()
        with pytest.raises(NotADirectoryError, match="路径不是目录"):
            registry.load_from_directory(file_path)

    def test_empty_directory(self, tmp_path: Path) -> None:
        """空目录返回 0。"""
        registry = StrategyRegistry()
        count = registry.load_from_directory(tmp_path)
        assert count == 0

    def test_skip_abstract_classes(self, tmp_path: Path) -> None:
        """跳过抽象类（未实现 on_bar）。"""
        strategy_file = tmp_path / "abstract_strat.py"
        strategy_file.write_text(
            textwrap.dedent("""\
                from abc import abstractmethod
                from app.domain.strategy.base import BaseStrategy
                from app.domain.backtest.order import OrderIntent
                from app.domain.backtest.engine_event import BarContext

                class AbstractStrat(BaseStrategy):
                    name = "abstract_one"

                    @abstractmethod
                    def custom_method(self):
                        ...

                    def on_bar(self, context: BarContext) -> list[OrderIntent]:
                        return []
            """),
            encoding="utf-8",
        )

        registry = StrategyRegistry()
        count = registry.load_from_directory(tmp_path)

        # AbstractStrat still has __abstractmethods__ due to custom_method
        assert count == 0


# ---------------------------------------------------------------------------
# Entry Point 加载测试
# ---------------------------------------------------------------------------


class TestLoadEntryPoints:
    """通过 entry_points 加载策略测试。"""

    def test_load_valid_entry_point(self) -> None:
        """成功加载有效的 entry_point。"""
        mock_ep = MagicMock()
        mock_ep.name = "alpha_ep"
        mock_ep.value = "test_module:AlphaStrategy"
        mock_ep.load.return_value = AlphaStrategy

        with patch(
            "app.domain.strategy.registry.entry_points",
            return_value=[mock_ep],
        ):
            registry = StrategyRegistry()
            count = registry.load_entry_points()

        assert count == 1
        assert registry.get("alpha") is AlphaStrategy

    def test_skip_invalid_entry_point(self) -> None:
        """跳过无效的 entry_point（非 BaseStrategy 子类）。"""
        mock_ep = MagicMock()
        mock_ep.name = "bad_ep"
        mock_ep.value = "test_module:NotAStrategy"
        mock_ep.load.return_value = str  # Not a strategy

        with patch(
            "app.domain.strategy.registry.entry_points",
            return_value=[mock_ep],
        ):
            registry = StrategyRegistry()
            count = registry.load_entry_points()

        assert count == 0

    def test_handle_entry_point_load_error(self) -> None:
        """entry_point 加载失败不阻塞。"""
        mock_ep = MagicMock()
        mock_ep.name = "broken_ep"
        mock_ep.value = "broken_module:BrokenStrategy"
        mock_ep.load.side_effect = ImportError("module not found")

        with patch(
            "app.domain.strategy.registry.entry_points",
            return_value=[mock_ep],
        ):
            registry = StrategyRegistry()
            count = registry.load_entry_points()

        assert count == 0

    def test_load_multiple_entry_points(self) -> None:
        """加载多个 entry_points。"""
        mock_ep1 = MagicMock()
        mock_ep1.name = "alpha_ep"
        mock_ep1.value = "test_module:AlphaStrategy"
        mock_ep1.load.return_value = AlphaStrategy

        mock_ep2 = MagicMock()
        mock_ep2.name = "beta_ep"
        mock_ep2.value = "test_module:BetaStrategy"
        mock_ep2.load.return_value = BetaStrategy

        with patch(
            "app.domain.strategy.registry.entry_points",
            return_value=[mock_ep1, mock_ep2],
        ):
            registry = StrategyRegistry()
            count = registry.load_entry_points()

        assert count == 2
        assert registry.get("alpha") is AlphaStrategy
        assert registry.get("beta") is BetaStrategy


# ---------------------------------------------------------------------------
# 内置策略加载测试
# ---------------------------------------------------------------------------


class TestLoadBuiltinStrategies:
    """内置策略加载测试。"""

    def test_load_builtin_strategies(self) -> None:
        """加载内置策略模块。"""
        registry = StrategyRegistry()
        count = registry.load_builtin_strategies()

        # 至少应该加载到一些内置策略
        assert count > 0
        # 验证一些已知的内置策略
        names = registry.list_names()
        assert len(names) > 0

    def test_builtin_strategies_are_valid(self) -> None:
        """内置策略都是有效的 BaseStrategy 子类。"""
        registry = StrategyRegistry()
        registry.load_builtin_strategies()

        for name, cls in registry.list_strategies().items():
            assert issubclass(cls, BaseStrategy)
            assert cls.name == name
            # 应该可以实例化
            instance = cls()
            assert instance is not None


# ---------------------------------------------------------------------------
# 全局注册表测试
# ---------------------------------------------------------------------------


class TestGlobalRegistry:
    """全局注册表测试。"""

    def setup_method(self) -> None:
        """每个测试前重置全局注册表。"""
        reset_global_registry()

    def teardown_method(self) -> None:
        """每个测试后重置全局注册表。"""
        reset_global_registry()

    def test_get_strategy_registry_singleton(self) -> None:
        """全局注册表是单例。"""
        reg1 = get_strategy_registry()
        reg2 = get_strategy_registry()
        assert reg1 is reg2

    def test_get_strategy_registry_loads_builtins(self) -> None:
        """全局注册表自动加载内置策略。"""
        registry = get_strategy_registry()
        assert registry.count > 0

    def test_reset_global_registry(self) -> None:
        """重置后重新获取会创建新实例。"""
        reg1 = get_strategy_registry()
        reset_global_registry()
        reg2 = get_strategy_registry()
        assert reg1 is not reg2
