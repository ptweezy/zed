import os
import platform
import statistics
import subprocess
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

binaries_dir, repositories_dir, summary_path, *variants = sys.argv[1:]
baseline = variants[0]
rounds = int(os.environ.get("BENCH_ROUNDS", "3"))
suffix = ".exe" if os.name == "nt" else ""
names = [
    name
    for name in [
        "linux-stale",
        "linux-shared-tip",
        "zed",
        "zed-behind",
        "linux",
        "linux-behind",
        "firefox",
        "linux-stale-detached",
    ]
    if os.path.isdir(os.path.join(repositories_dir, name))
]
modes = {
    "first": "First listing of a new repository, as after opening a project",
    "repeat": "Second listing of the same repository",
}
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
        shift = (round_number - 1) % len(variants)
        for variant in variants[shift:] + variants[:shift]:
            completed = subprocess.run(
                [os.path.join(binaries_dir, variant + suffix)],
                env=environment,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                sys.exit(f"{variant} failed:\n{completed.stdout}\n{completed.stderr}")
            for line in completed.stdout.splitlines():
                if "BENCH\t" not in line:
                    continue
                _, path, branch_count, first, repeat, head = line[line.index("BENCH\t") :].split("\t")
                name = os.path.basename(path.rstrip("/\\"))
                for mode, nanoseconds in [("first", int(first)), ("repeat", int(repeat))]:
                    samples[(name, variant, mode)].append(nanoseconds)
                    round_samples[(name, variant, mode, round_number)].append(nanoseconds)
                heads[(name, variant)].add(head)
                branch_counts[name] = int(branch_count)
                results.write(f"{round_number}\t{variant}\t{name}\t{branch_count}\t{first}\t{repeat}\t{head}\n")


def format_duration(nanoseconds):
    milliseconds = nanoseconds / 1e6
    if milliseconds >= 1000:
        return f"{milliseconds / 1000:.2f} s"
    if milliseconds >= 10:
        return f"{milliseconds:.1f} ms"
    return f"{milliseconds:.2f} ms"


git_version = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
others = variants[1:]
lines = [
    f"### {platform.system()} {platform.release()} ({platform.machine()}), {os.cpu_count()} CPUs, {git_version}",
    "",
    f"Median `branches()` time, release build, {rounds} rotating rounds of five new repositories listed twice.",
]
for mode, description in modes.items():
    lines += [
        "",
        f"#### {description}",
        "",
        "| Repository | Branches | "
        + " | ".join(variants)
        + " | "
        + " | ".join(f"{variant} speedup" for variant in others)
        + " | Same head tracking |",
        "|---|---|" + "---|" * (len(variants) + len(others)) + "---|",
    ]
    for name in names:
        medians = {variant: statistics.median(samples[(name, variant, mode)]) for variant in variants}
        same = all(heads[(name, variant)] == heads[(name, baseline)] for variant in variants)
        lines.append(
            f"| {name} | {branch_counts[name]:,} | "
            + " | ".join(format_duration(medians[variant]) for variant in variants)
            + " | "
            + " | ".join(f"{medians[baseline] / medians[variant]:,.2f}×" for variant in others)
            + f" | {'yes' if same else 'NO'} |"
        )
lines += ["", "| Repository | Variant | Listing | Per-round medians | Samples | Head tracking |", "|---|---|---|---|---|---|"]
for name in names:
    for variant in variants:
        for mode in modes:
            per_round = ", ".join(
                format_duration(statistics.median(round_samples[(name, variant, mode, round_number)]))
                for round_number in range(1, rounds + 1)
            )
            lines.append(
                f"| {name} | {variant} | {mode} | {per_round} | {len(samples[(name, variant, mode)])} "
                f"| {', '.join(sorted(heads[(name, variant)]))} |"
            )

summary = "\n".join(lines) + "\n"
print(summary)
with open(summary_path, "a", encoding="utf-8") as summary_file:
    summary_file.write(summary)
