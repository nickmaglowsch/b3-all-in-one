"""Construction and serialization contract for ParameterSpec.

ParameterSpec is what scripts/list_strategies.py turns into the JSON the
frontend builds its strategy forms from, so its field names, field *order* and
defaults are a wire contract, not an implementation detail. Strategies build
specs both positionally (config_strategy.py) and by keyword (teva_ativos_reais),
so both have to keep working.

Written to survive the switch from a hand-written __init__/__repr__ to a
dataclass: it pins the behaviour, not the mechanism.
"""
from __future__ import annotations

import pytest

from backtests.core.strategy_base import ParameterSpec
from backtests.core.strategy_registry import get_registry

# The positional order every call site in backtests/ relies on.
POSITIONAL_ORDER = ["name", "label", "param_type", "default"]


def test_positional_construction_keeps_its_field_order():
    s = ParameterSpec("min_adtv", "Min ADTV (BRL)", "float", 1_000_000.0)
    assert (s.name, s.label, s.param_type, s.default) == (
        "min_adtv", "Min ADTV (BRL)", "float", 1_000_000.0,
    )


def test_optional_fields_default_to_none_and_empty_description():
    s = ParameterSpec("x", "X", "int", 1)
    assert s.description == ""
    assert s.min_value is None and s.max_value is None
    assert s.step is None and s.choices is None


def test_keyword_construction_sets_every_optional_field():
    s = ParameterSpec(
        "rebalance_freq", "Rebalance Frequency", "choice", "ME",
        description="Portfolio rebalancing frequency",
        min_value=1, max_value=9, step=2, choices=["ME", "QE", "W-FRI"],
    )
    assert s.description == "Portfolio rebalancing frequency"
    assert (s.min_value, s.max_value, s.step) == (1, 9, 2)
    assert s.choices == ["ME", "QE", "W-FRI"]


def test_repr_names_the_type_and_default():
    """The repr is read in logs and in the REPL when a spec looks wrong; it has
    to identify which parameter it is and what it would default to."""
    r = repr(ParameterSpec("tax_rate", "Tax Rate", "float", 0.15))
    assert "tax_rate" in r and "float" in r and "0.15" in r


def test_every_registered_strategy_serializes_to_the_form_json():
    """The end-to-end shape: whatever list_strategies.py reads off a spec must be
    present on every spec of every discovered strategy. A renamed attribute here
    is a strategy form that silently loses its inputs."""
    reg = get_registry()
    seen = 0
    for strategy_name in reg.names():
        try:
            specs = reg.get(strategy_name).get_parameter_specs()
        except Exception:  # noqa: BLE001 — same guard list_strategies.py uses
            continue
        for spec in specs:
            payload = {
                "name": spec.name,
                "label": spec.label,
                "type": spec.param_type,
                "default": spec.default,
                "min": spec.min_value,
                "max": spec.max_value,
                "step": spec.step,
                "choices": spec.choices,
            }
            assert isinstance(payload["name"], str) and payload["name"]
            assert isinstance(payload["type"], str) and payload["type"]
            seen += 1
    assert seen > 50, f"suspiciously few parameters across the registry: {seen}"


def test_get_default_parameters_maps_name_to_default():
    class _Stub:
        get_parameter_specs = lambda self: [  # noqa: E731
            ParameterSpec("a", "A", "int", 1),
            ParameterSpec("b", "B", "float", 2.5),
        ]

    from backtests.core.strategy_base import StrategyBase

    assert StrategyBase.get_default_parameters(_Stub()) == {"a": 1, "b": 2.5}


@pytest.mark.parametrize("field", POSITIONAL_ORDER)
def test_required_fields_have_no_default(field):
    """A default on any of the four required fields would let a typo'd call site
    build a nameless parameter instead of failing loudly."""
    with pytest.raises(TypeError):
        ParameterSpec(**{f: "x" for f in POSITIONAL_ORDER if f != field})


def test_common_run_specs_are_the_five_shared_run_controls_in_order():
    from backtests.core import strategy_base as sb

    assert [s.name for s in sb.COMMON_RUN_SPECS] == [
        "start_date", "end_date", "initial_capital", "tax_rate", "slippage"]
    assert sb.COMMON_RUN_SPECS[0] is sb.COMMON_START_DATE
