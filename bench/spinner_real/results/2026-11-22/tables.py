#!/usr/bin/env python3
"""Compiles the PR table from measure_real.py logs: tables.py <log> [<log> ...]"""
import collections, json, statistics, sys

import os
LIMIT = float(os.environ.get("MAX_OTHER", "inf"))
rows = collections.defaultdict(list)
dropped = 0
for path in sys.argv[1:]:
    for line in open(path, errors="replace"):
        if line.startswith("{"):
            record = json.loads(line)
            if record.get("other", 0) > LIMIT:
                dropped += 1
                continue
            rows[(record["hz"], record["scenario"], record["variant"])].append(record)
print(f"dropped {dropped} runs with other > {LIMIT}")

def median(hz, scenario, variant, key):
    runs = rows.get((hz, scenario, variant))
    return statistics.median(run[key] for run in runs) if runs else None

def spread(hz, scenario, variant, key):
    runs = rows.get((hz, scenario, variant))
    values = [run[key] for run in runs]
    return f"{min(values):.0f}-{max(values):.0f}"

rates = sorted({key[0] for key in rows})
scenarios = []
for key in rows:
    if key[1] not in scenarios:
        scenarios.append(key[1])
variants = []
for key in rows:
    if key[2] not in variants:
        variants.append(key[2])
print("variants:", variants, " runs per cell:", sorted({len(v) for v in rows.values()}))
for key, label in [("zed", "Zed CPU ms/s"), ("fps", "Frames/s"), ("shell", "Compositor ms/s"), ("main", "Main thread ms/s")]:
    print(f"\n{label} (median; " + " → ".join(variants) + ")")
    print("| Scenario | " + " | ".join(f"{hz} Hz" for hz in rates) + " |")
    print("|---|" + "---|" * len(rates))
    for scenario in scenarios:
        cells = []
        for hz in rates:
            values = [median(hz, scenario, variant, key) for variant in variants]
            cells.append(" → ".join("n/a" if v is None else (f"{v:.1f}" if key == "fps" else f"{v:.0f}") for v in values))
        print(f"| {scenario} | " + " | ".join(cells) + " |")
print("\nZed CPU ms/s min-max per cell")
for scenario in scenarios:
    for hz in rates:
        print(f"  {hz:>3} Hz {scenario:13} " + "   ".join(f"{variant}: {spread(hz, scenario, variant, 'zed')}" for variant in variants if rows.get((hz, scenario, variant))))
