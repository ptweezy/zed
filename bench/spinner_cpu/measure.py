#!/usr/bin/env python3
"""Alternates before/after bench binaries and prints median CPU ms per second."""
import os, platform, re, statistics, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
runs = int(sys.argv[1]) if len(sys.argv) > 1 else 7
ext = ".exe" if os.name == "nt" else ""
test = "components::label::spinner_label::spinner_cpu_bench::bench_spinner_cpu"
configs = [(l, hz) for l in ["dots", "sand", "loading"] for hz in [60, 120]]
def run(binary, label, hz):
    env = dict(os.environ, BENCH_LABEL=label, BENCH_HZ=str(hz), BENCH_SECONDS="5")
    out = subprocess.run([os.path.join(here, "bin", binary + ext), "--ignored", "--exact", test, "--nocapture"],
                         env=env, capture_output=True, text=True).stdout
    m = re.search(r"renders_per_s=([\d.]+) cpu_ms_per_s=([\d.]+)", out)
    if not m: sys.exit(f"no result from {binary}:\n{out}")
    return float(m[1]), float(m[2])
print(f"platform: {platform.platform()} / {platform.processor() or platform.machine()} / {os.cpu_count()} CPUs")
print("| Label | Display | Renders/s before | after | CPU ms/s before | after | Change | After faster |")
print("|---|---|---|---|---|---|---|---|")
for label, hz in configs:
    res = {"before": [], "after": []}
    for i in range(runs):
        for b in (["before", "after"] if i % 2 == 0 else ["after", "before"]):
            res[b].append(run(b, label, hz))
    rb, ra = (statistics.median(r for r, _ in res[b]) for b in ("before", "after"))
    cb, ca = (statistics.median(c for _, c in res[b]) for b in ("before", "after"))
    wins = sum(a[1] < b[1] for a, b in zip(res["after"], res["before"]))
    print(f"| {label} | {hz} Hz | {rb:.1f} | {ra:.1f} | {cb:.1f} | {ca:.1f} | {100*(ca-cb)/cb:+.0f}% | {wins}/{runs} |", flush=True)
