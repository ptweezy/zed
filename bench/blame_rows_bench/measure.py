#!/usr/bin/env python3
"""Runs the blame-rows harness once per process, alternating which variant goes first each round.

usage: measure.py <binaries-dir> <fixtures-dir> <summary-file> <rounds> [<visible-rows> ...]

Writes every run to results.tsv and appends a Markdown table of medians to the summary file.
Exits with an error if the variants return different blame for any fixture.
"""

import glob
import os
import platform
import re
import statistics
import subprocess
import sys

VARIANTS = ["before", "after"]
# libtest prints the test's output on the same line as "test bench_blame_rows ... ".
FIELD = re.compile(r"(\w+)=(\S+)")


def run(binary, test, fixture, visible_rows):
    env = dict(os.environ, BENCH_FIXTURE=fixture, BENCH_ROWS=str(visible_rows))
    command = [binary, "--exact", test, "--nocapture", "--test-threads=1"]
    process = subprocess.run(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    lines = [line for line in process.stdout.splitlines() if "hash=" in line]
    if process.returncode != 0 or len(lines) != 1:
        sys.exit(f"{binary} {test} failed for {fixture}\n{process.stdout}")
    return dict(FIELD.findall(lines[0]))


def main():
    # Windows runners default to cp1252, which cannot encode the arrows in the summary.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    binaries_dir, fixtures_dir, summary_path, rounds = sys.argv[1:5]
    rounds = int(rounds)
    row_counts = [int(value) for value in sys.argv[5:]] or [60]
    suffix = ".exe" if os.name == "nt" else ""
    binaries = {variant: os.path.join(binaries_dir, variant + suffix) for variant in VARIANTS}
    counting = {
        variant: os.path.join(binaries_dir, variant + "-allocs" + suffix) for variant in VARIANTS
    }
    has_counting = all(os.path.exists(path) for path in counting.values())
    fixtures = glob.glob(os.path.join(fixtures_dir, "*.json"))

    checks = {}
    allocations = {}
    described = {}
    for fixture in fixtures:
        for variant in VARIANTS:
            fields = run(binaries[variant], "check_interleaved_buffers", fixture, row_counts[0])
            checks[fixture, variant] = (fields["hash"], fields["blamed_rows"])
            if has_counting:
                for visible_rows in row_counts:
                    fields = run(counting[variant], "bench_blame_rows", fixture, visible_rows)
                    allocations[fixture, visible_rows, variant] = float(fields["allocations"])
        if checks[fixture, "before"] != checks[fixture, "after"]:
            sys.exit(f"{fixture}: the variants disagree on interleaved buffers: {checks}")

    rows = []
    with open("results.tsv", "w", encoding="utf-8") as results:
        results.write("round\tfile\thunks\trows\tvariant\tmedian_us\tmean_us\tp10_us\tp90_us\thash\n")
        for round_index in range(rounds):
            order = VARIANTS if round_index % 2 == 0 else VARIANTS[::-1]
            for fixture in fixtures:
                for visible_rows in row_counts:
                    for variant in order:
                        fields = run(binaries[variant], "bench_blame_rows", fixture, visible_rows)
                        described[fixture] = (fields["file"], int(fields["hunks"]))
                        rows.append(
                            (fixture, visible_rows, variant, float(fields["median_us"]), fields["hash"])
                        )
                        results.write(
                            f"{round_index}\t{fields['file']}\t{fields['hunks']}\t{fields['rows']}\t"
                            f"{variant}\t{fields['median_us']}\t{fields['mean_us']}\t"
                            f"{fields['p10_us']}\t{fields['p90_us']}\t{fields['hash']}\n"
                        )
                        results.flush()
            print(f"round {round_index + 1}/{rounds} done", flush=True)

    summary = [
        f"{platform.platform()}, {platform.machine()}, {os.cpu_count()} CPUs, "
        f"{rounds} runs per variant, median of each run's median call",
        "",
        "| File | Hunks | Rows | Before | After | After faster | Allocations per call |",
        "|---|---|---|---|---|---|---|",
    ]
    for fixture in sorted(fixtures, key=lambda fixture: described[fixture][1]):
        name, hunks = described[fixture]
        for visible_rows in row_counts:
            selected = [row for row in rows if row[:2] == (fixture, visible_rows)]
            digests = {row[4] for row in selected}
            if len(digests) != 1:
                sys.exit(f"{name}: the variants returned different blame: {digests}")
            times = {
                variant: [row[3] for row in selected if row[2] == variant] for variant in VARIANTS
            }
            faster = sum(after < before for before, after in zip(times["before"], times["after"]))
            counts = "n/a"
            if has_counting:
                counts = " → ".join(
                    f"{allocations[fixture, visible_rows, variant]:,.0f}" for variant in VARIANTS
                )
            summary.append(
                f"| {name} | {hunks:,} | {visible_rows} "
                f"| {statistics.median(times['before']):.1f} µs "
                f"| {statistics.median(times['after']):.1f} µs | {faster}/{rounds} | {counts} |"
            )
    summary.append("")
    summary.append("The variants returned the same blame in every run and in the interleaved-buffer check.")
    output = "\n".join(summary) + "\n"
    with open(summary_path, "a", encoding="utf-8") as file:
        file.write(output)
    print(output)


main()
