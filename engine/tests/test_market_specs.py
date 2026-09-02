"""Every shipped spec declares a market, and every non-B3 one is runnable there.

The failure this exists for is silent: a spec that omits `market:` runs against
B3 whatever its name promises, so `USLowVol` would have produced a Brazilian
backtest and reported it under a US name. Nothing downstream would have said so.

Synthetic only — no DB, no network. The specs are read off disk rather than out
of the registry so a spec that fails to load is a failure here, not an absence.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from backtests.core import markets
from backtests.core.strategy_registry import get_registry

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "backtests" / "strategies" / "specs"

sys.path.insert(0, str(ROOT / "scripts"))
import validate_spec  # noqa: E402


def _specs() -> list:
    return sorted(SPECS.glob("*.yaml"))


def _ids(paths) -> list:
    return [p.stem for p in paths]


@pytest.mark.parametrize("path", _specs(), ids=_ids(_specs()))
def test_every_spec_names_a_market_the_registry_knows(path):
    spec = yaml.safe_load(path.read_text())
    # markets.get raises on an unknown key rather than defaulting, which is the
    # guard this asserts is reachable from every stored spec.
    assert markets.get(spec.get("market")).key in markets.keys()


@pytest.mark.parametrize("path", _specs(), ids=_ids(_specs()))
def test_every_spec_passes_the_validator_the_platform_shells(path):
    """The same check a tenant's saved spec goes through — which is also what
    rejects a crypto spec that reaches for fundamentals, so this covers that
    without needing to enumerate the crypto specs."""
    report = validate_spec.validate(yaml.safe_load(path.read_text()))
    assert report["ok"], report["errors"]


@pytest.mark.parametrize("path", _specs(), ids=_ids(_specs()))
def test_a_spec_filed_under_a_market_declares_it(path):
    """The filename prefix and the `market:` key must agree. A `us_*.yaml` that
    forgot the key is the exact silent-B3 failure above, and it reads as correct
    in every listing."""
    spec = yaml.safe_load(path.read_text())
    expected = {"us": "US", "crypto": "CRYPTO"}.get(path.stem.split("_")[0])
    if expected is None:
        return
    assert spec.get("market") == expected, (
        f"{path.name} is filed under {expected} but declares "
        f"market={spec.get('market')!r}")


def test_the_registry_serves_both_new_planes():
    reg = get_registry()
    by_market: dict[str, list] = {}
    for name in reg.names():
        by_market.setdefault(getattr(reg.get(name), "market", markets.DEFAULT), []).append(name)
    # Not an exact roster — that would break on every spec added — but each plane
    # has to be reachable from the gallery at all, which is the whole point.
    assert len(by_market.get("US", [])) >= 6, by_market.get("US")
    assert len(by_market.get("CRYPTO", [])) >= 6, by_market.get("CRYPTO")


@pytest.mark.parametrize("path", _specs(), ids=_ids(_specs()))
def test_no_off_b3_spec_asks_for_a_root_dedup(path):
    """`markets` gives US and CRYPTO `root_len: None`, so `dedup_roots` there is
    a no-op at best. Before the market reached keep_most_liquid_per_root it was
    worse than a no-op — a four-character root merged GOOG into GOOGL. Nothing
    shipped should rely on either behaviour."""
    spec = yaml.safe_load(path.read_text())
    if markets.get(spec.get("market")).root_len is not None:
        return
    assert not (spec.get("selection") or {}).get("dedup_roots"), (
        f"{path.name} sets dedup_roots on a market with no share-class roots")
