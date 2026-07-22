"""Clube do Valor "S-Rank" — the cheapest FIIs (P/VP + DY rank-sum).

Separate from the equity screen (rank_cheapest.py): FIIs are a different asset
class with their own snapshot and metrics. See methodologies/fii_cheapest.py.

Pipeline: eliminate (liquidez < R$200k, desenvolvimento, monoativo, prazo
determinado) → S-Rank survivors by P/VP (asc) + DY (desc) rank-sum → show a
candidate pool → drop unsustainable-yield funds (DY >> FFO) → backfill to a clean
top 15 (mirrors the equity screen's pool→clean flow). --no-trap-filter keeps them.

Mandato/prazo come from data/fii_metadata.csv (CVM via scripts.fetch_fii_metadata).
If that file is missing, the tipo/prazo filters are skipped (logged) — run the
fetcher first for the full screen.

Usage:
    python -m scripts.rank_fii_cheapest                  # top 15 (CdV's count)
    python -m scripts.rank_fii_cheapest --top 20
    python -m scripts.rank_fii_cheapest --refresh-metadata  # re-fetch CVM metadata first
    python -m scripts.rank_fii_cheapest --rebalance      # MANTER/VENDER/COMPRAR vs your holdings
    python -m scripts.rank_fii_cheapest --rebalance --exit-rank 25  # looser exit band

Rebalance mode reads your real FII holdings from data/positions.csv and applies
the CdV exit rule via a buffer band: a held fund is kept while it stays in the
clean top --exit-rank (default 22), sold only if it falls below that, gets
eliminated, or turns into a yield trap; the best non-held top-`top` names are buys;
aporte targets are the post-rebalance funds furthest below equal weight. Runs
semiannually.

Analysis only — not a buy/sell recommendation. Rebalance semiannually.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from methodologies import fii_cheapest

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "data" / "fii_snapshots"
METADATA = ROOT / "data" / "fii_metadata.csv"
POSITIONS = ROOT / "data" / "positions.csv"


def latest_snapshot() -> Path:
    files = sorted(SNAPSHOTS.glob("*.csv"))
    if not files:
        raise SystemExit("No FII snapshots in data/fii_snapshots/. Run fetch_fundamentus_fii.py first.")
    return files[-1]


def _rebalance(df: pd.DataFrame, ranked: pd.DataFrame, dropped: pd.DataFrame, args) -> int:
    """Compare current FII holdings (positions.csv) to the new ranking.

    Buffer rule: keep a held fund while it stays within the clean top `exit_rank`;
    sell it only if it falls below that band, gets eliminated by a filter, or turns
    into a yield trap. Buy the best non-held names from the clean top `top`.
    """
    if not POSITIONS.exists():
        raise SystemExit("data/positions.csv não encontrado. Rode load_b3.py primeiro.")
    pos = pd.read_csv(POSITIONS)
    pos["ticker"] = pos["ticker"].astype(str).str.upper()
    universe = set(df["papel"])
    held = pos[pos["ticker"].isin(universe)].copy()
    if held.empty:
        print("Nenhum FII detido encontrado em positions.csv (interseção com o snapshot).")
        return 0
    price = dict(zip(df["papel"], df["cotacao"]))
    held["mkt"] = held["qty"] * held["ticker"].map(price).fillna(0)

    clean = ranked[~ranked["is_trap"]].reset_index(drop=True)
    clean_rank = {t: i + 1 for i, t in enumerate(clean["papel"])}
    trap_set = set(ranked.loc[ranked["is_trap"], "papel"])
    trap_flags = dict(zip(ranked["papel"], ranked["yield_flags"]))
    elim_reason = dict(zip(dropped["papel"], dropped["elim_reason"])) if len(dropped) else {}

    rows = []
    for _, r in held.iterrows():
        t, mkt = r["ticker"], r["mkt"]
        if t in elim_reason:
            rows.append((t, "VENDER", "—", f"eliminado: {elim_reason[t]}", mkt))
        elif t in trap_set:
            rows.append((t, "VENDER", clean_rank.get(t, "—"),
                         f"yield trap: {trap_flags.get(t, '')}", mkt))
        elif t in clean_rank:
            rk = clean_rank[t]
            if rk <= args.exit_rank:
                rows.append((t, "MANTER", rk, f"dentro do top {args.exit_rank}", mkt))
            else:
                rows.append((t, "VENDER", rk, f"caiu p/ #{rk} (> top {args.exit_rank})", mkt))
        else:
            rows.append((t, "VENDER", "—", "fora do ranking elegível", mkt))
    reb = pd.DataFrame(rows, columns=["papel", "acao", "rank", "motivo", "mkt"])
    manter = reb[reb["acao"] == "MANTER"]
    vender = reb[reb["acao"] == "VENDER"]
    held_set = set(held["ticker"])
    comprar = [(t, clean_rank[t]) for t in clean["papel"].head(args.top) if t not in held_set]

    print(f"\n══ Rebalanceamento S-Rank · {len(held)} FIIs na carteira · "
          f"banda de saída: top {args.exit_rank} · alvo: {args.top} fundos ══")

    print(f"\n── MANTER ({len(manter)}) ──")
    if len(manter):
        print(tabulate(manter.sort_values("rank")[["papel", "rank", "motivo"]],
                       headers="keys", tablefmt="github", showindex=False))

    print(f"\n── VENDER ({len(vender)}) ──")
    print(tabulate(vender.sort_values("papel")[["papel", "rank", "motivo"]],
                   headers="keys", tablefmt="github", showindex=False) if len(vender) else "(nenhum)")

    print(f"\n── COMPRAR (top {args.top} não detidos: {len(comprar)}) ──")
    if comprar:
        print(tabulate(pd.DataFrame(comprar, columns=["papel", "rank"]).sort_values("rank"),
                       headers="keys", tablefmt="github", showindex=False))
    else:
        print("(nenhum — você já detém todos os top)")

    # Aporte: fund the post-rebalance portfolio toward equal weight (new buys = peso 0
    # → priorizados; depois os detidos mais abaixo do alvo).
    final = pd.concat([
        manter[["papel", "mkt"]],
        pd.DataFrame([(t, 0.0) for t, _ in comprar], columns=["papel", "mkt"]),
    ], ignore_index=True)
    tot = final["mkt"].sum()
    if len(final):
        final["peso"] = final["mkt"] / tot if tot > 0 else 0.0
        final["alvo"] = 1.0 / len(final)
        final["gap"] = final["alvo"] - final["peso"]
        ap = final.sort_values("gap", ascending=False).head(8)[["papel", "peso", "alvo", "gap"]].copy()
        for c in ("peso", "alvo", "gap"):
            ap[c] = (ap[c] * 100).round(1)
        print("\n── APORTE: priorizar os mais distantes do peso-alvo (equal-weight) ──")
        print(tabulate(ap, headers=["papel", "peso %", "alvo %", "gap %"],
                       tablefmt="github", showindex=False))

    # Honest guardrail: a near-total turnover means the strategy diverges from the
    # held portfolio, not that you should churn it all. Analysis, not a directive.
    if len(vender) >= 0.6 * len(held):
        print(f"\n⚠ Giro alto: {len(vender)}/{len(held)} da carteira sairia. O S-Rank é uma "
              f"estratégia de **deep value** (P/VP baixo + DY alto) e diverge de uma carteira de "
              f"FIIs de qualidade/baixo desconto como a sua. Vender tudo de uma vez = TROCA de "
              f"estratégia (custos + IR de 20% sobre ganho), não ajuste fino. Trate como análise: "
              f"decida se quer migrar de estilo, e se sim faça gradualmente. Não execute às cegas.")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=15, help="final clean portfolio size (default 15)")
    p.add_argument("--pool", type=int, default=0,
                   help="minimum candidate pool to display; auto-expanded so it always "
                        "contains at least `top` sustainable funds (default: auto)")
    p.add_argument("--no-trap-filter", action="store_true",
                   help="don't drop unsustainable-yield funds — show the raw top-`pool`")
    p.add_argument("--snapshot", type=str, help="path to a specific FII snapshot CSV")
    p.add_argument("--refresh-metadata", action="store_true",
                   help="re-fetch CVM mandato/prazo metadata before ranking")
    p.add_argument("--no-metadata", action="store_true",
                   help="ignore data/fii_metadata.csv (skip tipo/prazo filters)")
    p.add_argument("--rebalance", action="store_true",
                   help="compare your current FII holdings (data/positions.csv) to the new "
                        "ranking and output MANTER / VENDER / COMPRAR + aporte targets")
    p.add_argument("--exit-rank", type=int, default=22,
                   help="exit band: sell a held fund only if it falls below this clean rank "
                        "(default 22 ≈ 1.5× the 15-fund portfolio)")
    args = p.parse_args(argv[1:])

    if args.refresh_metadata:
        from scripts import fetch_fii_metadata
        fetch_fii_metadata.main(["fetch_fii_metadata"])

    snap = Path(args.snapshot) if args.snapshot else latest_snapshot()
    df = pd.read_csv(snap)
    print(f"FII snapshot: {snap.name} ({len(df)} funds)", file=sys.stderr)

    meta = None
    if not args.no_metadata and METADATA.exists():
        meta = pd.read_csv(METADATA, dtype=str)
        meta["mandato"] = meta["mandato"].fillna("")
        cov = df["papel"].isin(meta["ticker"]).mean()
        print(f"Metadata: {METADATA.name} ({len(meta)} tickers; "
              f"{cov:.0%} of this snapshot has mandato/prazo)", file=sys.stderr)
    else:
        print("Metadata: NONE — tipo-de-ativo and prazo filters SKIPPED. "
              "Run `python -m scripts.fetch_fii_metadata` for the full screen.", file=sys.stderr)

    kept, dropped = fii_cheapest.eliminate(df, meta)
    if len(dropped):
        counts = dropped["elim_reason"].value_counts()
        summ = ", ".join(f"{n} {r}" for r, n in counts.items())
        print(f"Eliminated {len(dropped)}/{len(df)}: {summ}", file=sys.stderr)

    ranked = fii_cheapest.flag_sustainability(fii_cheapest.rank(kept))

    if args.rebalance:
        return _rebalance(df, ranked, dropped, args)

    def render(frame: pd.DataFrame, *, with_flags: bool) -> str:
        cols = ["papel", "segmento", "cotacao", "pvp", "dy", "ffo_yield",
                "pvp_rank", "dy_rank", "srank", "qtd_imoveis"]
        if with_flags:
            cols.append("yield_flags")
        cols = [c for c in cols if c in frame.columns]
        v = frame[cols].copy()
        for c in v.columns:
            if pd.api.types.is_numeric_dtype(v[c]):
                v[c] = v[c].round(3)
        if with_flags and "yield_flags" in v.columns:
            v["yield_flags"] = v["yield_flags"].replace("", "—")
        return tabulate(v, headers="keys", tablefmt="github", showindex=False)

    print(f"\nFIIs Mais Baratos — CdV S-Rank (P/VP + DY rank-sum) · "
          f"{len(kept)} elegíveis após eliminação")

    if args.no_trap_filter:
        pool = ranked.head(args.pool or args.top)
        print(f"\n── Top {len(pool)} (raw, traps NÃO removidas) ──\n")
        print(render(pool, with_flags=True))
        return 0

    # Auto-size the pool so it contains at least `top` SUSTAINABLE funds — i.e.
    # extend down the ranking until the `top`-th non-trap appears, so dropping
    # traps still leaves a full clean list drawn from within the displayed pool.
    sustainable = (~ranked["is_trap"]).cumsum()
    if sustainable.max() >= args.top:
        pool_size = int((sustainable >= args.top).idxmax()) + 1
    else:
        pool_size = len(ranked)  # fewer than `top` sustainable funds exist
    pool_size = max(pool_size, min(args.pool, len(ranked)))
    pool = ranked.head(pool_size)
    n_unsust = int(pool["is_trap"].sum())

    print(f"\n── Pool candidato: top {len(pool)} por S-Rank "
          f"(expandido p/ conter {args.top} sustentáveis; "
          f"{n_unsust} yield traps removidos — DY>>FFO ou corte de distribuição) ──\n")
    print(render(pool, with_flags=True))

    clean = pool[~pool["is_trap"]].head(args.top).copy()
    print(f"\n── Carteira limpa {len(clean)}: removidos os yield traps (DY>>FFO / DY-cut) ──\n")
    print(render(clean, with_flags=True))
    if len(clean) < args.top:
        print(f"\n⚠ Só {len(clean)} fundos sustentáveis no universo elegível (< {args.top}).")

    still = clean[clean["dy"].fillna(0) > fii_cheapest.SUSPICIOUS_DY]
    if len(still):
        print(f"\nNota: {len(still)} na carteira limpa ainda têm DY>{fii_cheapest.SUSPICIOUS_DY:.0%} "
              f"({', '.join(still['papel'])}) — alto mas coberto por FFO. Verifique recorrência.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
