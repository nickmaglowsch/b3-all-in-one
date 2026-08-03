"""The JSON the platform shells this repo for.

`scripts/list_strategies.py`, `get_strategy_spec.py` and `list_signals.py` exist
solely because registry discovery has to happen inside the engine venv — the
platform never imports `backtests/`, it reads these three payloads. Their shape
is therefore an API, and nothing tested it: a renamed field or a swallowed
exception would surface as an empty gallery in the product, not a red build.
"""
from __future__ import annotations

import json

import pytest

from backtests.core.config_strategy import FixedWeight, RankAndHold
from backtests.core.strategy_registry import get_registry
from scripts import get_strategy_spec, list_signals, list_strategies

PARAM_KEYS = {"name", "label", "type", "default", "min", "max", "step", "choices"}


def emitted(capsys) -> object:
    return json.loads(capsys.readouterr().out)


def a_strategy(spec_kind: bool) -> str:
    reg = get_registry()
    for name in sorted(reg.names()):
        if isinstance(reg.get(name), (RankAndHold, FixedWeight)) is spec_kind:
            return name
    pytest.skip(f"no {'spec' if spec_kind else 'code'}-kind strategy registered")


# --------------------------------------------------------------------------
# list_strategies — the gallery
# --------------------------------------------------------------------------

def test_the_gallery_lists_every_registered_strategy(capsys):
    list_strategies.main()
    out = emitted(capsys)
    assert {s["name"] for s in out} == set(get_registry().names())
    assert out == sorted(out, key=lambda s: s["name"]), "gallery order must be stable"


def test_every_gallery_entry_carries_the_fields_the_platform_renders(capsys):
    list_strategies.main()
    for s in emitted(capsys):
        assert s["kind"] in ("spec", "code"), s["name"]
        assert isinstance(s["description"], str)
        assert "\n" not in s["description"], "description is collapsed to one line"
        assert isinstance(s["needs_fundamentals"], bool)
        for p in s["params"]:
            assert PARAM_KEYS == set(p), (s["name"], p)


def test_the_gallery_is_one_json_document_and_nothing_else(capsys):
    """It is read off stdout by a subprocess; a stray print would break parsing."""
    list_strategies.main()
    captured = capsys.readouterr()
    assert captured.out.startswith("[") and captured.out.rstrip().endswith("]")


def test_a_strategy_that_needs_fundamentals_says_so(capsys):
    """The platform gates on this to refuse a run before paying for a plane build."""
    list_strategies.main()
    flags = {s["name"]: s["needs_fundamentals"] for s in emitted(capsys)}
    assert any(flags.values()), "no strategy declares a fundamentals dependency"
    reg = get_registry()
    for name, flag in flags.items():
        assert flag == bool(getattr(reg.get(name), "needs_fundamentals", False))


# --------------------------------------------------------------------------
# get_strategy_spec — cloning a built-in into user space
# --------------------------------------------------------------------------

def run_spec(monkeypatch, name):
    monkeypatch.setattr("sys.argv", ["get_strategy_spec.py", "--name", name])
    get_strategy_spec.main()


def test_a_spec_strategy_hands_back_the_spec_it_was_built_from(monkeypatch, capsys):
    name = a_strategy(spec_kind=True)
    run_spec(monkeypatch, name)
    out = emitted(capsys)
    assert out["name"] == name
    assert out["kind"] in ("rank_and_hold", "fixed_weight")
    # The clone is only useful if it carries what params alone cannot express.
    assert out["spec"] == get_registry().get(name).spec


@pytest.mark.parametrize("case", ["code", "unknown"])
def test_there_is_nothing_to_clone_and_the_exit_code_says_so(monkeypatch, capsys, case):
    """The platform maps a non-zero exit here to a 404. A code-kind strategy has
    no data spec, and neither does a name that does not exist."""
    name = a_strategy(spec_kind=False) if case == "code" else "NoSuchStrategy"
    with pytest.raises(SystemExit) as e:
        run_spec(monkeypatch, name)
    assert e.value.code == 1
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------
# list_signals — the rule builder's factor picker
# --------------------------------------------------------------------------

def test_every_named_signal_is_offered_with_the_expression_behind_it(capsys):
    list_signals.main()
    out = emitted(capsys)
    assert out, "signals.yaml resolved to nothing"
    for sig in out:
        assert sig["name"] and isinstance(sig["expr"], str)
    assert len({s["name"] for s in out}) == len(out), "duplicate signal names"
