"""The MCP tool catalogue (spec/09): names, descriptions, input JSON Schemas.

What tools/list returns, verbatim. The names are the keys of
mcp_tools.HANDLERS — test_mcp asserts the two never drift apart. Descriptions
are written for the model that reads them: what the tool is for, when to call
it, and what comes back.
"""
from __future__ import annotations

# Not a oneOf: several clients flatten or drop it, and pydantic gives the model a
# precise message when a sleeve carries both keys or neither.
_SLEEVE = {
    "type": "object",
    "description": "Exactly one of strategy_ref / ticker, plus target_pct.",
    "properties": {
        "strategy_ref": {"type": "string", "description": (
            "A gallery strategy (built-in or your rank_and_hold) or CASH_CDI.")},
        "ticker": {"type": "string", "description": (
            "A listed B3 instrument held directly, e.g. IVVB11.SA, DIVO11.SA, "
            "BOVA11.SA. Its weight sits in CDI until its first price.")},
        "market": {"type": "string", "enum": ["B3"], "description": (
            "Optional, B3 only: a portfolio backtests on the B3 plane, so US and "
            "crypto tickers are refused.")},
        "target_pct": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
    },
    "required": ["target_pct"],
}
_SPEC = {
    "type": "object",
    "description": (
        "A strategy spec. kind=rank_and_hold: {kind, name, description, market, "
        "rebalance, rebalance_months?, weighting, weight_expr?, max_weight?, "
        "exposure_expr? (each held name keeps clip(value,0,1) of its weight, rest in "
        "CDI; e.g. ath_drawdown = depth below its own all-time high), "
        "universe{min_adtv,min_price,smallcap_below_median,sectors?{include,exclude},"
        "tickers?[codes: the universe is exactly these]}, "
        "signal{factor|expr|legs[{factor|expr, weight, transform}], coverage?, vars?, "
        "lookback, ...}, selection{top_pct|top_n, min_names, dedup_roots, min_hold, "
        "sticky}, regime?{inputs{name:{flag,offset,gt?}}, rules[{<input>:bool, "
        "top_pct?|exposure?(0..1, rest in CDI)|park?}]} (flags include bench_above "
        "and the numeric bench_drawdown, below the benchmark's all-time high)}. "
        "kind=fixed_weight: {kind, name, description, rebalance, "
        "rebalance_months?, sleeves[...]} with target_pct summing to 100, each "
        "sleeve exactly one of {strategy_ref, target_pct} (a gallery strategy or "
        "CASH_CDI) or {ticker, target_pct} (a listed B3 instrument, e.g. IVVB11.SA). "
        "Full reference: get_authoring_guide."),
    "properties": {
        "kind": {"type": "string", "enum": ["rank_and_hold", "fixed_weight"]},
        "name": {"type": "string", "maxLength": 120},
        "description": {"type": "string", "maxLength": 2000},
        "market": {"type": "string", "enum": ["B3", "US", "CRYPTO"]},
        "rebalance": {"type": "string", "enum": ["ME", "QE", "W-FRI"]},
        "rebalance_months": {"type": "array", "items": {"type": "integer", "minimum": 1,
                                                        "maximum": 12}},
        "weighting": {"type": "string", "enum": ["equal", "adtv", "expr"]},
        "weight_expr": {"type": "string", "maxLength": 2000},
        "max_weight": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        "universe": {"type": "object"},
        "signal": {"type": "object"},
        "selection": {"type": "object"},
        "regime": {"type": "object"},
        "sleeves": {"type": "array", "items": _SLEEVE},
    },
    "required": ["kind", "name"],
}
_REF = {"type": "string", "description": "Your strategy's name or id."}
_WAIT = {"type": "integer", "minimum": 0, "maximum": 300,
         "description": "Seconds to wait for the run to finish before returning."}
_RUN_ID = {"type": "string", "description": "A run_id from run_backtest/list_backtests."}
_METRIC = ["created_at", "ann_return", "sharpe", "calmar", "max_drawdown", "volatility"]


def _tool(name: str, description: str, properties: dict | None = None,
          required: list[str] | None = None, read_only: bool = True) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": properties or {},
                        "required": required or []},
        "annotations": {"readOnlyHint": read_only, "openWorldHint": False},
    }


TOOLS = [
    _tool("get_authoring_guide",
          "Read first. The full reference for writing strategies: spec fields, the "
          "signal expression language, regime overlays, markets, how backtests run "
          "and how to judge results without overfitting. Returns markdown."),
    _tool("list_signals",
          "Named factor signals usable as signal.factor, each with its expression "
          "and default parameters."),
    _tool("list_strategies",
          "The strategy gallery: built-in strategies plus the user's own. Use it to "
          "find baselines to beat and names to reference in portfolios.",
          {"source": {"type": "string", "enum": ["all", "builtin", "user"]},
           "market": {"type": "string", "enum": ["B3", "US", "CRYPTO"]},
           "include_params": {"type": "boolean",
                              "description": "Also list each strategy's run parameters."}}),
    _tool("get_strategy_spec",
          "The full spec of a config-driven strategy (built-in or yours) — the best "
          "starting point for a variant. Code-only built-ins have no spec (404).",
          {"name": {"type": "string"}}, ["name"]),
    _tool("list_my_strategies",
          "The user's saved strategies with their full specs and origin "
          "(\"mcp\" = created by an AI)."),
    _tool("validate_strategy",
          "Check a spec without saving it: schema, expression syntax, factor names, "
          "market/fundamentals compatibility, portfolio sleeve refs, that each "
          "universe.tickers code is priced on the spec's market plane, and that each "
          "ticker sleeve is priced on the B3 plane (an unknown ticker is an error "
          "naming it; one listed after history starts is a warning with its first "
          "price date). Returns the compiled spec and warnings.",
          {"spec": _SPEC}, ["spec"]),
    _tool("create_strategy",
          "Save a new strategy for the user: a rank_and_hold book, or a fixed_weight "
          "portfolio whose sleeves are strategies, CASH_CDI and/or listed tickers "
          "(e.g. IVVB11.SA). It appears in /bench under \"Minhas estratégias\" marked "
          "as created by AI, and can then be backtested by name. Same checks as "
          "validate_strategy.",
          {"spec": _SPEC}, ["spec"], read_only=False),
    _tool("update_strategy",
          "Replace one of the user's strategies with a new spec (renaming allowed; "
          "the kind cannot change).",
          {"strategy": _REF, "spec": _SPEC}, ["strategy", "spec"], read_only=False),
    _tool("delete_strategy",
          "Delete one of the user's strategies (refused while a live sleeve or a "
          "portfolio uses it). Past backtest runs are kept.",
          {"strategy": _REF}, ["strategy"], read_only=False),
    _tool("run_backtest",
          "Queue a backtest of any gallery strategy (built-in or yours) and, by "
          "default, wait up to 90s for it. Returns pre- and after-tax metrics when "
          "done, or the run_id to poll with get_backtest.",
          {"strategy": {"type": "string"},
           "start": {"type": "string", "format": "date", "description": "YYYY-MM-DD"},
           "end": {"type": "string", "format": "date", "description": "YYYY-MM-DD"},
           "freq": {"type": "string", "enum": ["ME", "QE", "W-FRI"],
                    "description": "Rebalance frequency override."},
           "params": {"type": "object",
                      "description": "Parameter overrides (lookback, top_pct, "
                                     "initial_capital, contribution, ...)."},
           "wait_seconds": _WAIT},
          ["strategy"], read_only=False),
    _tool("get_backtest",
          "One run's status and headline metrics; optionally wait for it to finish "
          "and/or include the engine's full metrics payload.",
          {"run_id": _RUN_ID, "wait_seconds": _WAIT, "full_metrics": {"type": "boolean"}},
          ["run_id"]),
    _tool("list_backtests",
          "The user's runs as a leaderboard: filter by strategy/status and rank by a "
          "metric (after-tax by default). The way to compare variants and baselines.",
          {"strategy": {"type": "string"},
           "status": {"type": "string", "enum": ["pending", "running", "done", "error"]},
           "sort_by": {"type": "string", "enum": _METRIC},
           "basis": {"type": "string", "enum": ["aftertax", "pretax"]},
           "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
    _tool("get_backtest_curve",
          "A finished run's equity curves (downsampled) and calendar-year returns — "
          "check a result is a steady edge, not one lucky year.",
          {"run_id": _RUN_ID,
           "max_points": {"type": "integer", "minimum": 2, "maximum": 500}},
          ["run_id"]),
]
