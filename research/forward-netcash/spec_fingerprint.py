"""Canonical fingerprint of a Quantax spec: the frozen fields, JSON with sorted keys, numbers as floats."""
import json, hashlib, sys
FIELDS = ("kind", "market", "rebalance", "weighting", "universe", "signal", "selection")
def norm(o):
    if isinstance(o, dict): return {k: norm(v) for k, v in sorted(o.items())}
    if isinstance(o, list): return [norm(v) for v in o]
    if isinstance(o, bool) or o is None or isinstance(o, str): return o
    if isinstance(o, (int, float)): return float(o)
    return o
def fingerprint(spec: dict) -> str:
    core = {k: norm(spec.get(k)) for k in FIELDS}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()
if __name__ == "__main__":
    print(fingerprint(json.load(open(sys.argv[1]))))
