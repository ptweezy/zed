#!/usr/bin/env python3
"""Tops up cells that lack quiet runs.

usage: fill.py <target runs> <max other ms/s> <out log> <log> [<log> ...]

Reads measure_real.py logs, counts the runs per (rate, scenario, variant) whose
"other" stayed at or below the limit, and measures the missing ones with the
same limit as the gate. Appends records in the same format to <out log>.
"""
import collections, json, os, subprocess, sys, time

OUT = os.path.expanduser("~/.cache/zed-pr-67261-feedback")
target, limit, out_path, logs = int(sys.argv[1]), float(sys.argv[2]), sys.argv[3], sys.argv[4:]
os.environ.setdefault("BENCH_ZED", f"{OUT}/bin/zed-steps")
os.environ.setdefault("BENCH_WORK", f"{OUT}/bench-work")
os.environ.setdefault("BENCH_WINDOW", "6")
os.environ["BENCH_MAX_OTHER"] = str(limit)
sys.path.insert(0, f"{OUT}/bench-wt/bench/spinner_real")
import measure_real as m

quiet = collections.Counter()
cells = []
for path in logs + ([out_path] if os.path.exists(out_path) else []):
    for line in open(path, errors="replace"):
        if not line.startswith("{"):
            continue
        record = json.loads(line)
        key = (record["hz"], record["scenario"], record["variant"])
        if key not in cells:
            cells.append(key)
        if record.get("other", 0) <= limit:
            quiet[key] += 1
missing = [(key, target - quiet[key]) for key in cells if quiet[key] < target]
print(f"{len(cells)} cells, {len(missing)} short of {target} quiet runs, {sum(count for _, count in missing)} runs to do", flush=True)
if not missing:
    sys.exit(0)

out = open(out_path, "a")
mocks = [subprocess.Popen([sys.executable, "-I", os.path.join(m.HERE, "mock_openai.py"), str(port), mode,
                           os.path.join(m.WORK, f"marker-{mode}")],
                          stderr=open(os.path.join(m.WORK, f"mock-{mode}.log"), "w"))
         for mode, port in m.PORTS.items()]
try:
    time.sleep(1)
    for hz in sorted({key[0] for key, _ in missing}):
        shell, shell_pid = m.start_shell(hz)
        try:
            m.run_once("hang", "after", shell_pid)
            todo = [[key, count] for key, count in missing if key[0] == hz]
            # Alternate cells so the variants of a scenario stay interleaved.
            while any(count for _, count in todo):
                for entry in todo:
                    if not entry[1]:
                        continue
                    _, scenario, variant = entry[0]
                    for attempt in range(40):
                        m.wait_for_quiet()
                        try:
                            result = m.run_once(scenario, variant, shell_pid)
                        except RuntimeError as error:
                            print(f"retrying {hz} {scenario} {variant}: {error}", flush=True)
                            continue
                        if result["other"] <= limit:
                            break
                        print(f"busy machine, repeating {hz} {scenario} {variant}: "
                              f"zed {result['zed']:.1f}, other {result['other']:.0f} ms/s", flush=True)
                    record = dict(hz=hz, scenario=scenario, variant=variant, index=100 + target - entry[1], **result)
                    out.write(json.dumps(record) + "\n")
                    out.flush()
                    print(json.dumps(record), flush=True)
                    entry[1] -= 1
        finally:
            m.stop_group(shell)
finally:
    for mock in mocks:
        mock.kill()
