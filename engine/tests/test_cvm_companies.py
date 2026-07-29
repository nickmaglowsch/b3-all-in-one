"""
Tests for CNPJ normalisation and the cnpj->ticker map (Task 02 — TDD).
"""
from __future__ import annotations

import sqlite3

import pytest

from b3_pipeline import storage
from b3_pipeline import cvm_storage
from b3_pipeline.b3_corporate_actions import _clean_cnpj
from tests.conftest import seed_cvm_company


@pytest.fixture
def mem_conn():
    """In-memory SQLite connection with full schema."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = OFF")
    storage.init_db(conn)
    yield conn
    conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# 1. _clean_cnpj — formatting stripped
# ──────────────────────────────────────────────────────────────────────────────

def test_clean_cnpj_strips_formatting():
    """CNPJ with dots/slashes/dashes should be returned as 14-digit string."""
    result = _clean_cnpj("33.000.167/0001-01")
    assert result == "33000167000101", f"Expected '33000167000101', got {result!r}"


def test_clean_cnpj_returns_none_for_missing():
    """A missing CNPJ should return None."""
    result = _clean_cnpj(None)
    assert result is None, f"Expected None, got {result!r}"


def test_clean_cnpj_returns_none_for_wrong_length():
    """CNPJ with wrong digit count should return None."""
    result = _clean_cnpj("123")
    assert result is None, f"Expected None for short CNPJ, got {result!r}"


# ──────────────────────────────────────────────────────────────────────────────
# 4. get_cvm_company_map — round trip
# ──────────────────────────────────────────────────────────────────────────────

def test_cvm_company_map_round_trip(mem_conn):
    """Inserted companies should appear in get_cvm_company_map output."""
    seed_cvm_company(
        mem_conn, cnpj="33000167000101", ticker="PETR",
        company_name="Petrobras", cvm_code="9512", b3_trading_name="PETROBRAS"
    )
    seed_cvm_company(
        mem_conn, cnpj="19526477000175", ticker="VALE",
        company_name="Vale", cvm_code="4170", b3_trading_name="VALE"
    )

    result = cvm_storage.get_cvm_company_map(mem_conn)
    assert result == {
        "33000167000101": "PETR",
        "19526477000175": "VALE",
    }, f"Unexpected map: {result}"


