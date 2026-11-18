import os
import platform
import statistics
import subprocess
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

binaries_dir, repositories_dir, summary_path = sys.argv[1:4]
rounds = int(os.environ.get("BENCH_ROUNDS", "3"))
suffix = ".exe" if os.name == "nt" else ""
names = [
    name
    for name in ["linux-stale", "zed", "linux", "firefox", "linux-stale-detached"]
    if os.path.isdir(os.path.join(repositories_dir, name))
]
environment = dict(
    os.environ,
    BENCH_REPOS=os.pathsep.join(os.path.join(repositories_dir, name) for name in names),
)

samples = defaultdict(list)
round_samples = defaultdict(list)
heads = defaultdict(set)
branch_counts = {}
with open("results.tsv", "w") as results:
    for round_number in range(1, rounds + 1):
        variants = ("before", "after") if round_number % 2 else ("after", "before")
        for variant in variants:
            completed = subprocess.run(
                [
                    os.path.join(binaries_dir, variant + suffix),
                    "repository::tests::bench_branches",
                    "--exact",
                    "--nocapture",
                    "--test-threads=1",
                ],
                env=environment,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                sys.exit(f"{variant} failed:\n{completed.stdout}\n{completed.stderr}")
            for line in completed.stdout.splitlines():
                if "BENCH\t" not in line:
                    continue
                _, path, branch_count, nanoseconds, head = line[line.index("BENCH\t") :].split("\t")
                name = os.path.basename(path.rstrip("/\\"))
                samples[(name, variant)].append(int(nanoseconds))
                round_samples[(name, variant, round_number)].append(int(nanoseconds))
                heads[(name, variant)].add(head)
                branch_counts[name] = int(branch_count)
                results.write(f"{round_number}\t{variant}\t{name}\t{branch_count}\t{nanoseconds}\t{head}\n")


def format_duration(nanoseconds):
    milliseconds = nanoseconds / 1e6
    if milliseconds >= 1000:
        return f"{milliseconds / 1000:.2f} s"
    if milliseconds >= 10:
        return f"{milliseconds:.1f} ms"
    return f"{milliseconds:.2f} ms"


git_version = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
lines = [
    f"### {platform.system()} {platform.release()} ({platform.machine()}), {os.cpu_count()} CPUs, {git_version}",
    "",
    f"Median `branches()` time, release build, {rounds} alternating rounds of five calls.",
    "",
    "| Repository | Branches | Before | After | Speedup | Same head tracking |",
    "|---|---|---|---|---|---|",
]
for name in names:
    before = statistics.median(samples[(name, "before")])
    after = statistics.median(samples[(name, "after")])
    same = heads[(name, "before")] == heads[(name, "after")]
    lines.append(
        f"| {name} | {branch_counts[name]:,} | {format_duration(before)} | {format_duration(after)} "
        f"| {before / after:,.2f}× | {'yes' if same else 'NO'} |"
    )
lines += ["", "| Repository | Variant | Per-round medians | Samples | Head tracking |", "|---|---|---|---|---|"]
for name in names:
    for variant in ("before", "after"):
        per_round = ", ".join(
            format_duration(statistics.median(round_samples[(name, variant, round_number)]))
            for round_number in range(1, rounds + 1)
        )
        lines.append(
            f"| {name} | {variant} | {per_round} | {len(samples[(name, variant)])} "
            f"| {', '.join(sorted(heads[(name, variant)]))} |"
        )

summary = "\n".join(lines) + "\n"
print(summary)
with open(summary_path, "a", encoding="utf-8") as summary_file:
    summary_file.write(summary)
