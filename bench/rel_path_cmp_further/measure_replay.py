"""Usage: measure_replay.py <cmpbench> <summary> <processes> <pairs-dir> <lists-dir> <variant,...>

Runs cmpbench in <processes> separate processes per tree, for two kinds of
input: the comparisons a Zed scan of that tree made (<pairs-dir>/<tree>.pairs.xz,
replayed in order), and the standard workloads on <lists-dir>/<tree>.txt.
Inside a process each round runs every variant once, rotating which goes first,
and the process reports the median over its rounds. The summary gives the
median over processes and how many processes had each variant faster than the
first variant.
"""

import lzma
import os
import platform
import statistics
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

TREES = ["zed", "linux", "firefox"]
WORKLOADS = {
    "replay": ("Comparisons from a scan, replayed", "ns/cmp"),
    "neighbours": ("Sorted neighbours", "ns/cmp"),
    "equal": ("Equal paths", "ns/cmp"),
    "random": ("Random pairs", "ns/cmp"),
    "search": ("Binary search for every path", "ns/search"),
    "sort_all": ("Sorting all paths", "ms"),
    "sort_siblings": ("Sorting each directory's children", "ns/path"),
}
REPLAY_ROUNDS = 11
LIST_ROUNDS = 7


def run(binary, arguments):
    completed = subprocess.run(
        [binary, *arguments], capture_output=True, text=True, encoding="utf-8"
    )
    if completed.returncode != 0:
        sys.exit(
            f"{binary} {arguments} exited with {completed.returncode}:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    header = next(line for line in completed.stdout.splitlines() if line.startswith("# "))
    values = {}
    for line in completed.stdout.splitlines():
        if line.startswith("@\t"):
            _, variant, workload, value = line.split("\t")
            values[(variant, workload)] = float(value)
    return header, values


def cpu_name():
    try:
        if sys.platform == "linux":
            for line in subprocess.run(
                ["lscpu"], capture_output=True, text=True, check=True
            ).stdout.splitlines():
                if line.startswith("Model name"):
                    return line.split(":", 1)[1].strip()
        elif sys.platform == "darwin":
            command = ["sysctl", "-n", "machdep.cpu.brand_string"]
            return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()
        elif os.name == "nt":
            command = ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"]
            return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    return platform.processor() or "unknown CPU"


def main():
    binary, summary_path, processes, pairs_dir, lists_dir, variants = sys.argv[1:]
    processes = int(processes)
    variants = variants.split(",")
    baseline, others = variants[0], variants[1:]
    selection = ",".join(variants)

    lines = [
        f"### {platform.system()} {platform.release()} ({platform.machine()}), "
        f"{os.cpu_count()} CPUs, {cpu_name()}",
        "",
        f"Median over {processes} processes; each process reports its median over "
        f"{REPLAY_ROUNDS} (replay) or {LIST_ROUNDS} rounds. 'faster' counts processes.",
        "",
        "| Tree | Workload | "
        + " | ".join(variants)
        + " | "
        + " | ".join(f"{variant} vs {baseline} | faster" for variant in others)
        + " |",
        "|---|---|" + "---|" * (len(variants) + 2 * len(others)),
    ]
    notes = []
    with tempfile.TemporaryDirectory() as scratch, open("results.tsv", "w") as results:
        results.write("tree\tprocess\tvariant\tworkload\tvalue\n")
        for tree in TREES:
            pairs = os.path.join(scratch, f"{tree}.pairs")
            with open(os.path.join(pairs_dir, f"{tree}.pairs.xz"), "rb") as packed:
                with open(pairs, "wb") as unpacked:
                    unpacked.write(lzma.decompress(packed.read()))
            path_list = os.path.join(lists_dir, f"{tree}.txt")
            samples = {}
            headers = set()
            for process in range(processes):
                for arguments in (
                    ["replay", pairs, str(REPLAY_ROUNDS), selection],
                    [path_list, str(LIST_ROUNDS), selection],
                ):
                    header, values = run(binary, arguments)
                    headers.add(header.split(":", 1)[1].rsplit(",", 1)[0].strip())
                    for (variant, workload), value in values.items():
                        samples.setdefault((variant, workload), []).append(value)
                        results.write(f"{tree}\t{process + 1}\t{variant}\t{workload}\t{value}\n")
                results.flush()
                print(f"{tree}: process {process + 1}/{processes}", file=sys.stderr, flush=True)
            os.remove(pairs)
            label = tree
            notes.append(f"- {tree}: " + "; ".join(sorted(headers)))
            for workload, (title, unit) in WORKLOADS.items():
                base_values = samples[(baseline, workload)]
                medians = {
                    variant: statistics.median(samples[(variant, workload)]) for variant in variants
                }
                comparisons = []
                for variant in others:
                    change = (medians[variant] / medians[baseline] - 1) * 100
                    faster = sum(
                        1
                        for base, other in zip(base_values, samples[(variant, workload)])
                        if other < base
                    )
                    comparisons.append(f"{change:+.1f}% | {faster}/{processes}")
                lines.append(
                    f"| {label} | {title} | "
                    + " | ".join(f"{medians[variant]:.2f} {unit}" for variant in variants)
                    + " | "
                    + " | ".join(comparisons)
                    + " |"
                )

    summary = "\n".join(lines + [""] + notes) + "\n"
    print(summary)
    with open(summary_path, "a", encoding="utf-8") as summary_file:
        summary_file.write(summary)


if __name__ == "__main__":
    main()
