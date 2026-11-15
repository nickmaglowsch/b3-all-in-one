"""Monthly forward-test check-in for "FWD Net-Cash Profitable".

Run from this directory:  python3 checkin.py
- verifies the saved spec still matches the frozen fingerprint (else: VOID)
- runs the frozen strategy and the benchmarks from the forward start to the latest data
- applies the pre-registered kill rules
- appends one row to LOG.md and saves raw run JSON under runs/
Every number written comes from a Quantax run made by this script.
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qtx_mcp import call  # noqa: E402
from spec_fingerprint import fingerprint  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
START = "2026-11-16"
FROZEN_FP = "c8bd7c0ba0ebe0d71b8894fdbd10518314afdc7cb8160847591a72921bfce9c2"
STRATEGY = "FWD Net-Cash Profitable"
BENCH = {"BOVA11": "GTC B_BOVA BOVA11 buy-hold", "DIVO11": "GTC B_DIVO DIVO11 buy-hold", "CDI": "GTC B_CDI cash"}
PARAMS = {"initial_capital": 100000, "contribution": 0, "slippage": 0.001}


def text(r):
    return "".join(c.get("text", "") for c in r["result"]["content"])


def run(name):
    d = json.loads(text(call("run_backtest", {"strategy": name, "start": START, "wait_seconds": 15, "params": PARAMS})))
    rid = d.get("run_id")
    for _ in range(40):
        if d.get("status") in ("done", "error", "failed"):
            break
        d = json.loads(text(call("get_backtest", {"run_id": rid, "wait_seconds": 25, "full_metrics": True})))
    return d


def summarize(d):
    if d.get("status") != "done":
        return {"status": d.get("status"), "error": str(d)[:300]}
    nav0 = PARAMS["initial_capital"]
    return {
        "status": "done",
        "end": d["window"]["end"],
        "invested_from": d["window"].get("invested_from"),
        "at_cum": (d["final_aftertax_nav"] / nav0 - 1) * 100,
        "pt_cum": (d["metrics"]["final_pretax_nav"] / nav0 - 1) * 100,
        "at_sharpe": d["aftertax"]["sharpe"],
        "mdd": d["aftertax"]["max_drawdown"],
        "tax": d["total_tax_paid"],
        "run_id": d["run_id"],
    }


def main():
    today = dt.date.today().isoformat()
    os.makedirs(os.path.join(HERE, "runs"), exist_ok=True)
    spec = json.loads(text(call("get_strategy_spec", {"name": STRATEGY})))["spec"]
    fp_ok = fingerprint(spec) == FROZEN_FP
    res = {}
    for label, name in [("FWD", STRATEGY)] + list(BENCH.items()):
        d = run(name)
        json.dump(d, open(os.path.join(HERE, "runs", f"{today}_{label}.json"), "w"), indent=1)
        res[label] = summarize(d)
    f, c = res["FWD"], res["CDI"]
    flags = []
    if not fp_ok:
        flags.append("VOID: saved spec no longer matches the frozen fingerprint")
    if f["status"] == "done" and c["status"] == "done":
        if f["mdd"] < -35:
            flags.append(f"KILL: drawdown {f['mdd']:.1f}% beyond -35%")
        if f["at_cum"] < c["at_cum"] - 15:
            flags.append(f"KILL: trails CDI by {c['at_cum'] - f['at_cum']:.1f} pp (> 15)")
    cdi_net = c["at_cum"] * 0.85 if c["status"] == "done" else float("nan")

    def cell(x, k, fmt="{:+.2f}%"):
        return fmt.format(x[k]) if x.get("status") == "done" else x.get("status", "?")

    cdi_net_s = f"{cdi_net:+.2f}%" if c["status"] == "done" else "-"
    row = (f"| {today} | {f.get('end', '-')} | {cell(f, 'at_cum')} | {cell(f, 'at_sharpe', '{:+.2f}')} | {cell(f, 'mdd', '{:.1f}%')} "
           f"| {cell(res['BOVA11'], 'at_cum')} | {cell(res['DIVO11'], 'at_cum')} | {cell(c, 'at_cum')} | {cdi_net_s} "
           f"| {'OK' if fp_ok else 'MISMATCH'} | {'; '.join(flags) or '-'} |")
    with open(os.path.join(HERE, "LOG.md"), "a") as fh:
        fh.write(row + "\n")
    print(row)
    print(json.dumps(res, indent=1))
    if flags:
        print("FLAGS:", flags)


if __name__ == "__main__":
    main()
