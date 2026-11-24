"""Usage: measure_edit_empty.py <binaries-dir> <summary-file> <rounds> <core|none> <variant> ...

Runs every bench_edit_empty workload in a process of its own, so one
workload's allocations can't change the next one's time. Each round runs
every variant once per workload, rotating which variant goes first. A variant
written as name@text runs only the workloads whose name contains the text.
With a core, pins every process to it (Linux only). Writes every sample to
results.tsv and appends a table of medians, with the range of the samples, to
the summary file.
"""
import os
import statistics
import subprocess
import sys

SUFFIX = ".exe" if os.name == "nt" else ""


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    binaries_dir, summary_path, rounds, core, *variants = sys.argv[1:]
    rounds = int(rounds)
    prefix = [] if core == "none" else ["taskset", "-c", core]
    filters = dict((variant.split("@") + [""])[:2] for variant in variants)
    variants = list(filters)
    binaries = {variant: os.path.join(binaries_dir, variant + SUFFIX) for variant in variants}
    listing = subprocess.run([binaries[variants[0]], "list"], capture_output=True, text=True, check=True)
    workloads = listing.stdout.splitlines()
    samples = {(workload, variant): [] for workload in workloads for variant in variants}
    with open("results.tsv", "w") as results:
        results.write("round\tvariant\tworkload\tnanoseconds\n")
        for round_index in range(rounds):
            shift = round_index % len(variants)
            for workload in workloads:
                for variant in variants[shift:] + variants[:shift]:
                    if filters[variant] not in workload:
                        continue
                    completed = subprocess.run(
                        prefix + [binaries[variant], workload],
                        capture_output=True, text=True, check=True,
                    )
                    value = float(completed.stdout.strip())
                    samples[(workload, variant)].append(value)
                    results.write(f"{round_index + 1}\t{variant}\t{workload}\t{value}\n")
            results.flush()
            print(f"round {round_index + 1}/{rounds}", file=sys.stderr, flush=True)
    lines = [
        f"Median of {rounds} rounds in nanoseconds, with the lowest and highest sample.",
        "",
        "| Workload | " + " | ".join(variants) + " |",
        "|---|" + "---|" * len(variants),
    ]
    for workload in workloads:
        cells = []
        for variant in variants:
            values = sorted(samples[(workload, variant)])
            if not values:
                cells.append("")
                continue
            cells.append(f"{statistics.median(values):.1f} ({values[0]:.1f}–{values[-1]:.1f})")
        lines.append(f"| {workload} | " + " | ".join(cells) + " |")
    table = "\n".join(lines) + "\n"
    print(table)
    if summary_path != "-":
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(table)


if __name__ == "__main__":
    main()
