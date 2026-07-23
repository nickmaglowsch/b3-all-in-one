"""PUT/GET /portfolio/sleeves — atomic replace + validation (spec/05 Feature 3)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_sleeves_put_get_and_validation(client):
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Valid: a real gallery strategy + the risk-free sleeve, summing to 100.
    good = [{"strategy_ref": "MomSharpe", "target_pct": 60},
            {"strategy_ref": "CASH_CDI", "target_pct": 40}]
    r = await client.put("/portfolio/sleeves", json=good, headers=headers)
    assert r.status_code == 200, r.text

    got = (await client.get("/portfolio/sleeves", headers=headers)).json()
    assert {s["strategy_ref"]: s["target_pct"] for s in got} == \
        {"MomSharpe": 60, "CASH_CDI": 40}

    # Sum != 100 → 422.
    bad_sum = [{"strategy_ref": "MomSharpe", "target_pct": 70},
               {"strategy_ref": "CASH_CDI", "target_pct": 40}]
    assert (await client.put("/portfolio/sleeves", json=bad_sum,
                             headers=headers)).status_code == 422

    # Unknown strategy → 422.
    bad_ref = [{"strategy_ref": "NotARealStrategy", "target_pct": 100}]
    assert (await client.put("/portfolio/sleeves", json=bad_ref,
                             headers=headers)).status_code == 422

    # Rejected PUTs never touched the stored set (atomic replace only on success).
    still = (await client.get("/portfolio/sleeves", headers=headers)).json()
    assert {s["strategy_ref"] for s in still} == {"MomSharpe", "CASH_CDI"}
