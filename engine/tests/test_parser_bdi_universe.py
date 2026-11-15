"""Which COTAHIST instruments reach the price plane (config.EQUITY_BDI_CODES).

The whitelist was {"02"} — lote padrão only — which meant no FII, FIAGRO or ETF
ever got a row in `prices`, so any book holding them reported them as "sem
cotação na base". These tests pin the classes that must be in and the one that
must stay out (fracionário, folded onto its round-lot parent at read time).

Reuses the fixed-width line builder from the fatcot tests rather than growing a
second copy of the COTAHIST layout.
"""
from tests.test_parser_fatcot import _build_cotahist_line, _make_zip_with_lines

from b3_pipeline.parser import parse_cotahist_file


def _tickers(lines, tmp_path):
    df = parse_cotahist_file(_make_zip_with_lines(lines, tmp_path))
    return set() if df.empty else set(df["ticker"])


def test_funds_and_etfs_reach_the_plane(tmp_path):
    """FII (12), FIAGRO (13) and ETF (14) are spot instruments a retail book
    holds for years. Dropping them left them permanently unpriced."""
    lines = [
        _build_cotahist_line(cod_bdi="02", ticker="PETR4"),
        _build_cotahist_line(cod_bdi="12", ticker="BTLG11"),
        _build_cotahist_line(cod_bdi="13", ticker="KNCA11"),
        _build_cotahist_line(cod_bdi="14", ticker="IVVB11"),
        _build_cotahist_line(cod_bdi="10", ticker="ITSA2"),
    ]
    assert _tickers(lines, tmp_path) == {"PETR4", "BTLG11", "KNCA11", "IVVB11", "ITSA2"}


def test_fractional_market_is_not_stored(tmp_path):
    """PETR4F is PETR4 at a smaller lot — same closes. Storing it would double
    the plane for no new information, so it is consolidated at read time."""
    lines = [
        _build_cotahist_line(cod_bdi="02", ticker="PETR4", tipo_mercado="010"),
        _build_cotahist_line(cod_bdi="96", ticker="PETR4F", tipo_mercado="020"),
    ]
    assert _tickers(lines, tmp_path) == {"PETR4"}


def test_derivatives_stay_out(tmp_path):
    """Options (78/82) and termo (62) are not positions the index can mark."""
    lines = [
        _build_cotahist_line(cod_bdi="02", ticker="PETR4", tipo_mercado="010"),
        _build_cotahist_line(cod_bdi="78", ticker="PETRA100", tipo_mercado="070"),
        _build_cotahist_line(cod_bdi="62", ticker="PETR4T", tipo_mercado="030"),
    ]
    assert _tickers(lines, tmp_path) == {"PETR4"}


def test_index_lines_are_not_stored(tmp_path):
    """IBOV11 is the Ibovespa in points, printed as a lote-padrão row with an
    "IND" ISIN. These are its real 2025 rows around 2025-03-05, when B3 moved its
    quotation factor from 1 to 100: stored, they read as a -99% day and as the
    most-traded name on the board. An ETF on the index (BOVA11) is a holding and
    stays."""
    lines = [
        _build_cotahist_line(date_str="20250226", cod_bdi="02", ticker="IBOV11", fatcot=1,
                             close_price_raw=12_445_200, isin_code="BRIBOVINDM18"),
        _build_cotahist_line(date_str="20250305", cod_bdi="02", ticker="IBOV11", fatcot=100,
                             close_price_raw=12_310_700, isin_code="BRIBOVINDM18"),
        _build_cotahist_line(date_str="20250305", cod_bdi="02", ticker="SMLL11", fatcot=100,
                             isin_code="BRSMLLINDM18"),
        _build_cotahist_line(date_str="20250305", cod_bdi="14", ticker="BOVA11", fatcot=1,
                             isin_code="BRBOVACTF003"),
    ]
    assert _tickers(lines, tmp_path) == {"BOVA11"}
