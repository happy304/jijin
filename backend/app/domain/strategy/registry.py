"""自定义策略注册模块。

提供策略的发现、加载与注册功能：
- 通过 Python entry_points（setuptools）加载第三方策略包
- 通过目录扫描加载用户本地策略文件
- 内置策略自动注册
- 统一的策略注册表，支持查询所有可用策略

设计要点：
- 所有注册的策略必须继承 BaseStrategy
- 加载失败的策略记录警告但不阻塞其他策略加载
- 支持通过环境变量 STRATEGY_PLUGINS_DIR 配置自定义策略目录

需求: 5.9, 10.5
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

from importlib.metadata import entry_points

from app.domain.strategy.base import BaseStrategy

logger = logging.getLogger(__name__)

# Entry point group name for strategy plugins
STRATEGY_ENTRY_POINT_GROUP = "fundquant.strategies"


class StrategyRegistry:
    """策略注册表。

    管理所有可用策略（内置 + 自定义），提供注册、查询、加载功能。

    Usage::

        registry = StrategyRegistry()
        registry.register(MyStrategy)
        registry.load_entry_points()
        registry.load_from_directory("/path/to/strategies")

        all_strategies = registry.list_strategies()
        cls = registry.get("my_strategy")
    """

    def __init__(self) -> None:
        self._strategies: dict[str, type[BaseStrategy]] = {}

    def register(self, strategy_cls: type[BaseStrategy]) -> None:
        """注册一个策略类。

        Args:
            strategy_cls: 策略类，必须继承 BaseStrategy

        Raises:
            TypeError: 如果 strategy_cls 不是 BaseStrategy 的子类
            ValueError: 如果策略类没有定义 name 属性或 name 为空
        """
        if not isinstance(strategy_cls, type) or not issubclass(strategy_cls, BaseStrategy):
            raise TypeError(
                f"策略类必须继承 BaseStrategy，收到: {strategy_cls!r}"
            )

        # BaseStrategy 本身不应被注册
        if strategy_cls is BaseStrategy:
            raise TypeError("不能注册 BaseStrategy 抽象类本身")

        name = getattr(strategy_cls, "name", None)
        if not name or name == "unnamed_strategy":
            raise ValueError(
                f"策略类 {strategy_cls.__name__} 必须定义非空的 name 属性"
            )

        if name in self._strategies:
            existing = self._strategies[name]
            logger.warning(
                "策略名称 '%s' 已被 %s 注册，将被 %s 覆盖",
                name,
                existing.__name__,
                strategy_cls.__name__,
            )

        self._strategies[name] = strategy_cls
        logger.debug("已注册策略: %s (%s)", name, strategy_cls.__name__)

    def unregister(self, name: str) -> bool:
        """取消注册一个策略。

        Args:
            name: 策略名称

        Returns:
            True 如果成功移除，False 如果策略不存在
        """
        if name in self._strategies:
            del self._strategies[name]
            return True
        return False

    def get(self, name: str) -> type[BaseStrategy] | None:
        """根据名称获取策略类。

        Args:
            name: 策略名称

        Returns:
            策略类，如果不存在返回 None
        """
        return self._strategies.get(name)

    def list_strategies(self) -> dict[str, type[BaseStrategy]]:
        """列出所有已注册的策略。

        Returns:
            策略名称到策略类的映射（副本）
        """
        return dict(self._strategies)

    def list_names(self) -> list[str]:
        """列出所有已注册策略的名称。

        Returns:
            策略名称列表（已排序）
        """
        return sorted(self._strategies.keys())

    @property
    def count(self) -> int:
        """已注册策略数量。"""
        return len(self._strategies)

    def clear(self) -> None:
        """清空注册表。主要用于测试。"""
        self._strategies.clear()

    # ------------------------------------------------------------------
    # 内置策略加载
    # ------------------------------------------------------------------

    def load_builtin_strategies(self) -> int:
        """加载内置策略。

        扫描 app.domain.strategy 包下的已知策略模块，
        自动注册所有 BaseStrategy 子类。

        Returns:
            成功注册的策略数量
        """
        builtin_modules = [
            "app.domain.strategy.dca",
            "app.domain.strategy.momentum",
            "app.domain.strategy.risk_parity",
            "app.domain.strategy.mean_variance",
            "app.domain.strategy.timing",
            "app.domain.strategy.fof",
            "app.domain.strategy.mean_reversion",
        ]

        count = 0
        for module_name in builtin_modules:
            try:
                module = importlib.import_module(module_name)
                count += self._register_from_module(module)
            except Exception as e:
                logger.warning("加载内置策略模块 %s 失败: %s", module_name, e)

        return count

    # ------------------------------------------------------------------
    # Entry Point 加载
    # ------------------------------------------------------------------

    def load_entry_points(self) -> int:
        """通过 setuptools entry_points 加载第三方策略。

        第三方包可以在其 pyproject.toml 中声明：

            [project.entry-points."fundquant.strategies"]
            my_strategy = "my_package.strategies:MyStrategy"

        Returns:
            成功注册的策略数量
        """
        count = 0
        eps = entry_points(group=STRATEGY_ENTRY_POINT_GROUP)

        for ep in eps:
            try:
                obj = ep.load()
                if isinstance(obj, type) and issubclass(obj, BaseStrategy) and obj is not BaseStrategy:
                    self.register(obj)
                    count += 1
                    logger.info(
                        "通过 entry_point 加载策略: %s (from %s)",
                        getattr(obj, "name", ep.name),
                        ep.value,
                    )
                else:
                    logger.warning(
                        "Entry point '%s' 指向的对象不是有效的 BaseStrategy 子类: %r",
                        ep.name,
                        obj,
                    )
            except Exception as e:
                logger.warning(
                    "加载 entry_point '%s' 失败: %s",
                    ep.name,
                    e,
                )

        return count

    # ------------------------------------------------------------------
    # 目录扫描加载
    # ------------------------------------------------------------------

    def load_from_directory(self, directory: str | Path) -> int:
        """从指定目录扫描并加载策略。

        扫描目录下所有 .py 文件（非递归），导入模块并注册
        其中所有 BaseStrategy 子类。

        文件名以 _ 开头的会被跳过（视为私有/辅助模块）。

        Args:
            directory: 策略文件所在目录路径

        Returns:
            成功注册的策略数量

        Raises:
            FileNotFoundError: 如果目录不存在
            NotADirectoryError: 如果路径不是目录
        """
        dir_path = Path(directory)

        if not dir_path.exists():
            raise FileNotFoundError(f"策略目录不存在: {dir_path}")

        if not dir_path.is_dir():
            raise NotADirectoryError(f"路径不是目录: {dir_path}")

        count = 0
        py_files = sorted(dir_path.glob("*.py"))

        for py_file in py_files:
            # 跳过私有模块和 __init__.py
            if py_file.name.startswith("_"):
                continue

            try:
                module = self._import_file(py_file)
                count += self._register_from_module(module)
            except Exception as e:
                logger.warning(
                    "从文件 %s 加载策略失败: %s",
                    py_file,
                    e,
                )

        return count

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _register_from_module(self, module: ModuleType) -> int:
        """从模块中发现并注册所有 BaseStrategy 子类。

        Args:
            module: 已导入的 Python 模块

        Returns:
            成功注册的策略数量
        """
        count = 0

        for attr_name in dir(module):
            obj = getattr(module, attr_name)

            # 跳过非类对象
            if not isinstance(obj, type):
                continue

            # 跳过 BaseStrategy 本身和非子类
            if obj is BaseStrategy or not issubclass(obj, BaseStrategy):
                continue

            # 跳过抽象类（没有实现 on_bar）
            if getattr(obj, "__abstractmethods__", None):
                continue

            # 跳过没有有效 name 的类
            name = getattr(obj, "name", None)
            if not name or name == "unnamed_strategy":
                continue

            # 跳过从其他模块导入的类（避免重复注册）
            if getattr(obj, "__module__", None) != module.__name__:
                continue

            try:
                self.register(obj)
                count += 1
            except (TypeError, ValueError) as e:
                logger.warning(
                    "注册策略 %s 失败: %s",
                    obj.__name__,
                    e,
                )

        return count

    def _import_file(self, file_path: Path) -> ModuleType:
        """从文件路径动态导入模块。

        Args:
            file_path: Python 文件路径

        Returns:
            导入的模块对象

        Raises:
            ImportError: 如果导入失败
        """
        module_name = f"_strategy_plugin_{file_path.stem}"

        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法为 {file_path} 创建模块规格")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module

        try:
            spec.loader.exec_module(module)
        except Exception:
            # 清理失败的模块
            sys.modules.pop(module_name, None)
            raise

        return module


# ---------------------------------------------------------------------------
# 全局注册表实例
# ---------------------------------------------------------------------------

_global_registry: StrategyRegistry | None = None


def get_strategy_registry() -> StrategyRegistry:
    """获取全局策略注册表实例。

    首次调用时会自动加载内置策略和 entry_points。

    Returns:
        全局 StrategyRegistry 实例
    """
    global _global_registry

    if _global_registry is None:
        _global_registry = StrategyRegistry()
        _global_registry.load_builtin_strategies()
        _global_registry.load_entry_points()

    return _global_registry


def reset_global_registry() -> None:
    """重置全局注册表。主要用于测试。"""
    global _global_registry
    _global_registry = None
