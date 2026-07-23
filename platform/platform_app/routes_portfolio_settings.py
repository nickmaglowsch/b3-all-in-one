"""Per-tenant portfolio settings: asset-class overrides (e.g. mark LFTB11 as a
cash/CDI-equivalent) and rebalance behavior (buy-only-laggards, harvest-only-sells,
cadence). Filled in by the portfolio-settings feature wave.

Owned by the settings agent. Registered in main.py as `portfolio_settings_router`.
"""
from fastapi import APIRouter

portfolio_settings_router = APIRouter(prefix="/portfolio/settings", tags=["portfolio-settings"])
