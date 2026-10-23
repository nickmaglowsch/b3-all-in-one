"""The MCP tool catalogue (spec/09): names, descriptions, input JSON Schemas.

What tools/list returns, verbatim. The names are the keys of
mcp_tools.HANDLERS — test_mcp asserts the two never drift apart. Descriptions
are written for the model that reads them: what the tool is for, when to call
it, and what comes back.
"""
from __future__ import annotations

_SPEC = {
    "type": "object",
    "description": (
        "A strategy spec. kind=rank_and_hold: {kind, name, description, market, "
        "rebalance, weighting, universe{min_adtv,min_price,smallcap_below_median}, "
        "signal{factor|expr, lookback, ...}, selection{top_pct|top_n, min_names, "
        "dedup_roots, min_hold, sticky}, regime?}. kind=fixed_weight: {kind, name, "
        "description, rebalance, sleeves[{strategy_ref, target_pct}]} summing to 100. "
        "Full reference: get_authoring_guide."),
    "properties": {
        "kind": {"type": "string", "enum": ["rank_and_hold", "fixed_weight"]},
        "name": {"type": "string", "maxLength": 120},
        "description": {"type": "string", "maxLength": 2000},
        "market": {"type": "string", "enum": ["B3", "US", "CRYPTO"]},
        "rebalance": {"type": "string", "enum": ["ME", "QE", "W-FRI"]},
        "weighting": {"type": "string", "enum": ["equal", "adtv"]},
        "universe": {"type": "object"},
        "signal": {"type": "object"},
        "selection": {"type": "object"},
        "regime": {"type": "object"},
        "sleeves": {"type": "array", "items": {"type": "object"}},
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
          "market/fundamentals compatibility. Returns the compiled spec and warnings.",
          {"spec": _SPEC}, ["spec"]),
    _tool("create_strategy",
          "Save a new strategy for the user. It appears in /bench under \"Minhas "
          "estratégias\" marked as created by AI, and can then be backtested by name.",
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
