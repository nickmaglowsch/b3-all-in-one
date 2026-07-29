"""Executable Gherkin for the monthly apuração — features/apuracao_mensal.feature.

Why here and not in `platform`: these are the rules a contador has to be able to
check without reading Python, and they are pure functions of a DataFrame. The
platform's routes are async and DB-bound, so a scenario there would be mostly
fixture plumbing and very little specification.

The scenarios cover the same ground as test_apuracao.py by design — that file is
the fine-grained unit suite, this one is the readable contract on top of it.

    uv run pytest tests/test_apuracao_bdd.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.apuracao import monthly_apuracao  # noqa: E402
from test_apuracao import grid, pick  # noqa: E402  same dir, pytest puts it on sys.path

scenarios("features/apuracao_mensal.feature")

MES, SEGUINTE = 1, 2


@pytest.fixture
def livro() -> dict:
    """month number -> {classe: (gross, pnl)}, exactly what `grid` takes."""
    return {}


@given(parsers.parse(
    'um mês com vendas de "{classe}" de R$ {gross:f} e lucro de R$ {pnl:f}'))
def mes(livro, classe, gross, pnl):
    livro[str(MES)] = {classe: (gross, pnl)}
    livro["classe"] = classe


@given(parsers.parse(
    'o mês seguinte com vendas de "{classe}" de R$ {gross:f} e lucro de R$ {pnl:f}'))
def mes_seguinte(livro, classe, gross, pnl):
    livro[str(SEGUINTE)] = {classe: (gross, pnl)}
    livro["classe"] = classe


def _apurar(livro, month):
    classe = livro.pop("classe")
    rows = monthly_apuracao(grid(**livro))
    livro["classe"] = classe
    return pick(rows, classe, month)


@when("eu apuro o mês", target_fixture="apurado")
def apuro_o_mes(livro):
    return _apurar(livro, MES)


@when("eu apuro o mês seguinte", target_fixture="apurado")
def apuro_o_mes_seguinte(livro):
    return _apurar(livro, SEGUINTE)


@then("o mês é isento")
def isento(apurado):
    assert apurado["isento"] is True


@then("o mês não é isento")
def nao_isento(apurado):
    assert apurado["isento"] is False


@then(parsers.parse("o IR devido é R$ {esperado:f}"))
def ir_devido(apurado, esperado):
    assert apurado["ir"] == pytest.approx(esperado)


@then(parsers.parse("o ganho isento é R$ {esperado:f}"))
def ganho_isento(apurado, esperado):
    assert apurado["ganho_isento"] == pytest.approx(esperado)


@then(parsers.parse("o prejuízo acumulado ao fim do mês é R$ {esperado:f}"))
def prejuizo_acumulado(apurado, esperado):
    assert apurado["carry"] == pytest.approx(esperado)
