#!/usr/bin/env python3
"""Structural quality gates: cyclomatic complexity, module size, dependency boundaries.

A ratchet, not a big-bang cleanup. `quality_baseline.json` records every offender
that exists today; the gate fails when one gets *worse* or a new one appears.
Fix something, run `--update`, and the baseline can never drift back up.

    python scripts/quality_gates.py            # check (CI)
    python scripts/quality_gates.py --update   # re-record after an improvement

Complexity comes from ruff's mccabe for Python and eslint's `complexity` rule for
the frontend; module size covers .py and .ts/.tsx alike. The frontend half needs
frontend/node_modules — see ts_complexity().
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).with_name("quality_baseline.json")

# Source roots. Tests are deliberately out: a long, branchy test is a fine test.
PY_SOURCES = [
    "platform/platform_app",
    "taxcore/scripts",
    "engine/b3_pipeline",
    "engine/backtests",
    "engine/scripts",
    # The two non-B3 ingest packages. They were outside the gate — and outside
    # CI's --cov list — from the day they were written, which is how a whole
    # market's pipeline can grow without anything measuring it. Every module in
    # both is comfortably inside the limits today, so this costs nothing now and
    # stops the next one drifting.
    "engine/us_pipeline",
    "engine/crypto_pipeline",
]
TS_SOURCES = ["frontend/app", "frontend/components", "frontend/lib"]
# Scanned for references only (orphans()), never gated on themselves.
TEST_SOURCES = ["platform/tests", "engine/tests", "taxcore/tests", "scripts"]

MAX_COMPLEXITY = 10
MAX_MODULE_LINES = 400

# Modules discovered at runtime instead of imported by name. The strategy registry
# walks its package with pkgutil.iter_modules (strategy_registry.discover) and
# alembic loads migrations by revision id, so in both cases a module nothing
# imports is still reachable — without this the orphan gate reports all twenty
# strategies and every migration.
DYNAMIC_ROOTS = ["engine/backtests/strategies", "platform/platform_app/migrations"]

# Standalone modules that no code imports or shells, kept on purpose. Empty, and
# the entry costs a line of justification, because that is the point: three of
# these accumulated unnoticed (a SQLite-era DB auditor, a superseded backtest
# runner, and a credentialed CVM channel) and each one read as load-bearing.
ENTRY_POINTS: dict[str, str] = {}

# (importer root, forbidden top-level module, why). All hold today — there is no
# baseline for these, a violation is always a regression.
CONTRACTS = [
    ("engine/b3_pipeline", "backtests",
     "the data plane must not depend on the strategies that read it"),
    ("platform/platform_app", "b3_pipeline",
     "platform shells into the engine venv (enginerun), it never imports it"),
    ("platform/platform_app", "backtests",
     "platform shells into the engine venv (enginerun), it never imports it"),
    ("platform/platform_app", "scripts",
     "platform shells into the taxcore venv (b3run), it never imports it"),
]


def _py_files(roots: list[str]):
    for root in roots:
        yield from sorted((ROOT / root).rglob("*.py"))


def _ts_files(roots: list[str]):
    for root in roots:
        base = ROOT / root
        for pat in ("*.ts", "*.tsx"):
            yield from sorted(p for p in base.rglob(pat) if "node_modules" not in p.parts)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def ts_complexity() -> dict[str, int] | None:
    """Same, for the frontend, via eslint's `complexity` rule.

    None when frontend/node_modules is absent — eslint cannot run, and reporting the
    frontend as clean would be a lie. The caller drops frontend keys from the
    comparison entirely in that case, so a skipped check never reads as an
    improvement. CI installs the deps, so there the check is always real.
    """
    if not (ROOT / "frontend" / "node_modules").is_dir():
        return None
    proc = subprocess.run(
        ["npx", "eslint", "--format", "json",
         *(s.removeprefix("frontend/") for s in TS_SOURCES)],
        cwd=ROOT / "frontend", capture_output=True, text=True,
    )
    if not proc.stdout.strip():
        sys.exit(f"eslint produced no report: {proc.stderr.strip()}")
    out = {}
    for f in json.loads(proc.stdout):
        # A file that does not parse yields no complexity messages at all, so every
        # entry it has in the baseline would read as "now under the limit" — the gate
        # would report a complexity-43 function as an *improvement* and exit 0. Same
        # invariant as the node_modules guard above: never infer clean from silence.
        fatal = [m for m in f["messages"] if m.get("fatal")]
        if fatal:
            sys.exit(f"eslint could not parse {_rel(Path(f['filePath']))}: "
                     f"{fatal[0].get('message', '').strip()}")
        for m in f["messages"]:
            if m.get("ruleId") != "complexity":
                continue
            # "Function 'ContaPage' has a complexity of 16. Maximum allowed is 10"
            # Anonymous arrows have no name, so they key on their line — which means
            # inserting code above one shows up as a new offender. It fails safe
            # (re-run --update), and there is exactly one such function today.
            name = m["message"].split("'")[1] if "'" in m["message"] else f"line{m['line']}"
            key = f"{_rel(Path(f['filePath']))}::{name}"
            _record(out, key, int(m["message"].split("complexity of ")[1].split(".")[0]))
    return out


def _record(out: dict[str, int], key: str, value: int) -> None:
    """Add one offender, refusing to let a second one overwrite the first.

    `path::function` is not unique — two `def run` in one module collide (ruff reports
    nested functions too, so a second `def _fmt` is enough), and the loser's regression
    would then be invisible. There are no collisions today; this fails loudly on the
    day someone introduces one rather than silently passing.

    Renaming is the fix, not a line-numbered key: keying on `lineno` would make every
    insertion above an over-complex function look like a brand-new offender, which
    fires the ratchet on unrelated PRs. Only the anonymous arrows in ts_complexity()
    pay that cost, because eslint gives them no name to use instead.
    """
    if key in out:
        sys.exit(f"ambiguous key {key!r}: two functions of that name in one module — "
                 "the gate cannot tell their complexities apart. Rename one.")
    out[key] = value


def complexity() -> dict[str, int]:
    """path::function -> mccabe complexity, for everything over MAX_COMPLEXITY."""
    ruff = ["ruff"] if shutil.which("ruff") else ["uvx", "ruff"]
    proc = subprocess.run(
        [*ruff, "check", "--isolated", "--select", "C901", "--output-format", "json",
         "--config", f"lint.mccabe.max-complexity = {MAX_COMPLEXITY}", *PY_SOURCES],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode not in (0, 1):  # 1 == violations found, which is the point
        sys.exit(f"ruff failed: {proc.stderr.strip()}")
    out = {}
    for v in json.loads(proc.stdout or "[]"):
        # "`run_pipeline` is too complex (43 > 10)"
        name, sep, tail = v["message"].partition("` is too complex (")
        # ruff reports syntax errors whatever `--select` says, and those carry no
        # separator. Say so instead of dying on int("") three lines down.
        if not sep:
            sys.exit(f"ruff could not parse {v.get('filename', '?')}: {v['message']}")
        # ruff reports absolute paths; the baseline must survive a different checkout dir.
        key = f"{_rel(Path(v['filename']))}::{name.lstrip('`')}"
        _record(out, key, int(tail.split(" >")[0]))
    return out


def module_lines() -> dict[str, int]:
    """path -> line count, for everything over MAX_MODULE_LINES."""
    files = [*_py_files(PY_SOURCES), *_ts_files(TS_SOURCES)]
    counts = {_rel(p): len(p.read_text(encoding="utf-8").splitlines()) for p in files}
    return {k: n for k, n in counts.items() if n > MAX_MODULE_LINES}


def _refs(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(module names imported, string literals that are not docstrings).

    A module is reached one of two ways: something imports it, or a live code path
    names it as a subprocess target — b3run hands ``"scripts.load_b3"`` to
    ``python -m``, enginerun hands ``"bench_series.py"`` to the engine interpreter.

    Docstrings are excluded on purpose. ``run_single_backtest.py`` was named in two
    strategy docstrings and nowhere else; a module someone wrote a sentence about is
    not a module anything runs.
    """
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            first = body[0].value
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                docstrings.add(id(first))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.update(a.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            imported.update((node.module or "").split("."))
            imported.update(a.name for a in node.names)

    strings = {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    }
    return imported, strings


def orphans() -> list[str]:
    """Source modules that nothing imports and no code path shells.

    Scans every .py in the repo, tests included — a module only its own test
    imports is still counted as reached. This gate is deliberately the loose
    version: it fires only on code with no reference anywhere at all, which is
    the state dead vendored subtrees actually decay into.
    """
    # Per file, so a module's own text cannot vouch for it. audit_db.py names
    # itself in an error message ("audit_db.py is a sqlite-only diagnostic") and
    # a repo-wide reference set would have read that as a caller.
    refs = {
        _rel(p): _refs(ast.parse(p.read_text(encoding="utf-8"), filename=str(p)))
        for p in _py_files([*PY_SOURCES, *TEST_SOURCES])
    }

    dynamic = {ROOT / d for d in DYNAMIC_ROOTS}
    out = []
    for path in _py_files(PY_SOURCES):
        if path.name == "__init__.py" or any(d in path.parents for d in dynamic):
            continue
        rel, stem = _rel(path), path.stem
        if rel in ENTRY_POINTS:
            continue
        # Substring, not equality, on the string side: the reference is
        # "scripts.load_b3" or "bench_series.py", never the bare stem. Loose on
        # purpose — this gate should not cry wolf.
        if any(
            stem in imported or any(stem in s for s in strings)
            for other, (imported, strings) in refs.items() if other != rel
        ):
            continue
        out.append(rel)
    return out


def contract_breaks() -> list[str]:
    breaks = []
    for importer, forbidden, why in CONTRACTS:
        for path in _py_files([importer]):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    roots = [(node.module or "").split(".")[0]]
                else:
                    continue
                if forbidden in roots:
                    breaks.append(
                        f"{_rel(path)}:{node.lineno}: imports `{forbidden}` — {why}"
                    )
    return breaks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="re-record the baseline")
    args = ap.parse_args()

    ts = ts_complexity()
    found = {"complexity": {**complexity(), **(ts or {})},
             "module_lines": module_lines()}

    if args.update:
        if ts is None:
            sys.exit("refusing to update: frontend/node_modules missing, so the "
                     "frontend complexity entries would be silently dropped. "
                     "Run `npm --prefix frontend ci` first.")
        BASELINE.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n")
        print(f"baseline updated: {sum(len(v) for v in found.values())} entries")
        return 0

    base = json.loads(BASELINE.read_text())
    failures, improved = [], []
    if ts is None:
        print("  SKIPPED   frontend complexity — run `npm --prefix frontend ci`\n")
    for gate, limit in (("complexity", MAX_COMPLEXITY), ("module_lines", MAX_MODULE_LINES)):
        old, new = base.get(gate, {}), found[gate]
        if ts is None and gate == "complexity":
            # eslint never ran: drop the frontend side of both so a skipped check
            # cannot read as "every frontend function got simpler".
            old = {k: v for k, v in old.items() if not k.startswith("frontend/")}
        for key, value in sorted(new.items()):
            was = old.get(key)
            if was is None:
                failures.append(f"{gate}: {key} = {value} (limit {limit}) — new offender")
            elif value > was:
                failures.append(f"{gate}: {key} = {value}, was {was} — got worse")
            elif value < was:
                improved.append(f"{gate}: {key} {was} -> {value}")
        for key in sorted(set(old) - set(new)):
            improved.append(f"{gate}: {key} now under the limit")

    failures += [f"dependency: {b}" for b in contract_breaks()]
    failures += [
        f"orphan: {m} — nothing imports it and no code path shells it. Delete it, "
        f"or add it to ENTRY_POINTS with the reason it stays."
        for m in orphans()
    ]

    for line in improved:
        print(f"  improved  {line}")
    if improved:
        print("\nrun `python scripts/quality_gates.py --update` to lock these in.\n")
    for line in failures:
        print(f"  FAIL      {line}")
    print(
        f"\n{len(failures)} failure(s); baselined debt: "
        f"{len(found['complexity'])} over-complex functions, "
        f"{len(found['module_lines'])} oversized modules."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
