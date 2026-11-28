#!/usr/bin/env python3
"""Runs the sort-lines harness once per process, alternating which variant goes first each round.

usage: measure.py <binaries-dir> <summary-file> <rounds> <action>:<text>[:<lines>] ...

Writes every run to results.tsv and appends a Markdown table of medians to the summary file.
Exits with an error if the variants leave different text in the buffer.
"""

import os
import platform
import re
import statistics
import subprocess
import sys

VARIANTS = ["before", "after"]
# libtest prints the test's output on the same line as "test bench_sort_lines ... ".
PATTERN = re.compile(r"BENCH .* bytes=(\d+) ms=([0-9.]+) hash=([0-9a-f]+)")


def run(binary, action, text, lines):
    env = dict(os.environ, BENCH_ACTION=action, BENCH_TEXT=text.removesuffix("-sorted"))
    env["BENCH_LINES"] = str(lines)
    env.pop("BENCH_PRESORTED", None)
    if text.endswith("-sorted"):
        env["BENCH_PRESORTED"] = "1"
    command = [binary, "--exact", "bench_sort_lines", "--nocapture", "--test-threads=1"]
    process = subprocess.run(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    match = PATTERN.search(process.stdout)
    if process.returncode != 0 or not match:
        sys.exit(f"{binary} failed for {action}:{text}:{lines}\n{process.stdout}")
    return int(match.group(1)), float(match.group(2)), match.group(3)


def main():
    binaries_dir, summary_path, rounds = sys.argv[1], sys.argv[2], int(sys.argv[3])
    workloads = []
    for spec in sys.argv[4:]:
        parts = spec.split(":")
        workloads.append((parts[0], parts[1], int(parts[2]) if len(parts) > 2 else 100_000))
    suffix = ".exe" if os.name == "nt" else ""
    binaries = {variant: os.path.join(binaries_dir, variant + suffix) for variant in VARIANTS}

    rows = []
    with open("results.tsv", "w", encoding="utf-8") as results:
        results.write("round\taction\ttext\tlines\tvariant\tbytes\tms\thash\n")
        for round_index in range(rounds):
            order = VARIANTS if round_index % 2 == 0 else VARIANTS[::-1]
            for action, text, lines in workloads:
                for variant in order:
                    size, ms, digest = run(binaries[variant], action, text, lines)
                    rows.append((round_index, action, text, lines, variant, size, ms, digest))
                    results.write(
                        f"{round_index}\t{action}\t{text}\t{lines}\t{variant}\t{size}\t{ms}\t{digest}\n"
                    )
                    results.flush()
            print(f"round {round_index + 1}/{rounds} done", flush=True)

    summary = [
        f"{platform.platform()}, {platform.machine()}, {os.cpu_count()} CPUs, "
        f"{rounds} runs per variant",
        "",
        "| Action | Text | Lines | MB | Before | After | After faster |",
        "|---|---|---|---|---|---|---|",
    ]
    for action, text, lines in workloads:
        selected = [row for row in rows if row[1:4] == (action, text, lines)]
        digests = {row[7] for row in selected}
        if len(digests) != 1:
            sys.exit(f"{action}:{text}:{lines}: the variants left different text: {digests}")
        times = {variant: [row[6] for row in selected if row[4] == variant] for variant in VARIANTS}
        faster = sum(after < before for before, after in zip(times["before"], times["after"]))
        summary.append(
            f"| {action} | {text} | {lines:,} | {selected[0][5] / 1e6:.1f} "
            f"| {statistics.median(times['before']):.1f} ms "
            f"| {statistics.median(times['after']):.1f} ms | {faster}/{rounds} |"
        )
    output = "\n".join(summary) + "\n"
    print(output)
    with open(summary_path, "a", encoding="utf-8") as file:
        file.write(output)


main()
