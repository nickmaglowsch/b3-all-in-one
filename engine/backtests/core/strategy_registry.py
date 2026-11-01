"""
Strategy Registry
=================
Discovers and manages strategy plugin classes from backtests/strategies/.
"""
from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

from backtests.core.strategy_base import StrategyBase

SPECS_DIR = Path(__file__).resolve().parent.parent / "strategies" / "specs"


class StrategyRegistry:
    """Discovers and manages strategy plugins."""

    def __init__(self) -> None:
        self._strategies: dict[str, StrategyBase] = {}

    def register(self, strategy: StrategyBase) -> None:
        """Register a strategy instance."""
        self._strategies[strategy.name] = strategy

    def get(self, name: str) -> StrategyBase:
        """Retrieve a strategy by name. Raises KeyError if not found."""
        return self._strategies[name]

    def names(self) -> list[str]:
        """Return list of strategy names."""
        return list(self._strategies.keys())

    def discover(self, package_path: str = "backtests.strategies") -> None:
        """
        Auto-discover strategy classes from the strategies package.
        Imports all modules in the package and registers any StrategyBase subclass found.
        """
        try:
            pkg = importlib.import_module(package_path)
        except ImportError:
            return

        pkg_path = getattr(pkg, "__path__", None)
        if pkg_path is None:
            return

        for _importer, modname, _ispkg in pkgutil.iter_modules(pkg_path):
            try:
                module = importlib.import_module(f"{package_path}.{modname}")
            except Exception as e:
                logger.warning("Failed to import strategy module %s.%s: %s", package_path, modname, e)
                continue

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, StrategyBase)
                    and attr is not StrategyBase
                ):
                    try:
                        instance = attr()
                        # Abstract bases (e.g. _SMATiltBase) satisfy the ABC but
                        # carry a sentinel empty name — registering one puts a
                        # blank row in the UI picker and a '' key in the registry.
                        if not str(instance.name).strip():
                            continue
                        self.register(instance)
                    except Exception as e:
                        logger.warning("Failed to instantiate strategy %s.%s: %s", modname, attr_name, e)

        # Config-driven strategies from backtests/strategies/specs/*.yaml
        self._load_specs(SPECS_DIR)

        # Per-tenant user specs, materialized by the platform into a scratch dir
        # (JSON-as-YAML). Built-ins win on name collision: only register a user
        # spec whose name is not already taken.
        user_dir = os.environ.get("ENGINE_USER_SPECS_DIR")
        if user_dir:
            self._load_specs(Path(user_dir), user=True)
        self._inherit_data_needs()

    def _inherit_data_needs(self) -> None:
        """A spec needs CVM fundamentals if any `strategy` sleeve does, at any depth.

        The runners load fundamentals only when the strategy they run says so
        (needs_fundamentals), and a spec only says what its author wrote: the
        platform's validate_spec infers the flag from f_* keys, which a sleeve
        naming a code strategy (TevaAtivosReais) has none of. So a user blend
        holding Teva ran without share counts and its sleeve never invested.
        Resolved here, after every strategy is registered — a spec cannot ask
        the registry while it is being built.
        """
        def needs(strat, seen: frozenset) -> bool:
            sleeves = (getattr(strat, "spec", None) or {}).get("sleeves") or []
            subs = [sl["strategy"] for sl in sleeves
                    if isinstance(sl, dict) and sl.get("strategy") in self._strategies]
            return bool(getattr(strat, "needs_fundamentals", False)) or any(
                n not in seen and needs(self._strategies[n], seen | {n}) for n in subs)

        for strat in self._strategies.values():
            if getattr(strat, "spec", None) is not None:
                strat.needs_fundamentals = needs(strat, frozenset({strat.name}))

    def _load_specs(self, spec_dir: Path, user: bool = False) -> None:
        """Register every ``*.yaml`` spec in ``spec_dir``, warning past bad ones."""
        from backtests.core.config_strategy import RankAndHold, FixedWeight

        kinds = {"rank_and_hold": RankAndHold, "fixed_weight": FixedWeight}
        for path in sorted(spec_dir.glob("*.yaml")) if spec_dir.is_dir() else []:
            try:
                spec = yaml.safe_load(path.read_text())
                if user and spec["name"] in self._strategies:
                    continue
                self.register(kinds[spec["kind"]](spec))
            except Exception as e:
                logger.warning("Failed to load %sstrategy spec %s: %s",
                               "user " if user else "", path.name, e)

# ── Global singleton ──────────────────────────────────────────────────────────

_registry: Optional[StrategyRegistry] = None


def get_registry() -> StrategyRegistry:
    """Return the global strategy registry, discovering strategies on first call."""
    global _registry
    if _registry is None:
        # Build into a local and publish only after discover() fully succeeds.
        # If discovery raises partway (e.g. spec loading), we must NOT cache a
        # half-built registry (plugins-but-no-specs) — that would stick for the
        # whole process and silently drop every YAML strategy. Leaving _registry
        # None lets the next call retry cleanly.
        reg = StrategyRegistry()
        reg.discover()
        _registry = reg
    return _registry
