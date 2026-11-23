"""Usage: measure_edit_isolated.py <binaries-dir> <summary-path> <rounds> <repetitions> <variant> ...

Runs every bench_edit workload in a process of its own (BENCH_ONLY), because
one workload's allocations change the next one's time. Each round runs every
variant once per workload, rotating which variant goes first. Prints medians
over the rounds in nanoseconds per inserted item, with each variant's change
against the first.
"""

import os
import platform
import statistics
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

SUFFIX = ".exe" if os.name == "nt" else ""


def cpu_name():
    try:
        if sys.platform == "linux":
            # /proc/cpuinfo has no model name on aarch64.
            for line in subprocess.run(["lscpu"], capture_output=True, text=True, check=True).stdout.splitlines():
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


def run(binary, repetitions, workload=None):
    environment = dict(os.environ)
    if workload is not None:
        environment["BENCH_ONLY"] = workload
    completed = subprocess.run(
        [binary, str(repetitions)], capture_output=True, text=True, encoding="utf-8", env=environment
    )
    if completed.returncode != 0:
        sys.exit(f"{binary} exited with {completed.returncode}:\n{completed.stdout}\n{completed.stderr}")
    return [line.split("\t") for line in completed.stdout.splitlines()]


def main():
    binaries_dir, summary_path, rounds, repetitions, *variants = sys.argv[1:]
    rounds, repetitions = int(rounds), int(repetitions)
    binaries = {variant: os.path.join(binaries_dir, variant + SUFFIX) for variant in variants}
    workloads = [name for name, _ in run(binaries[variants[-1]], 1)]
    samples = {(workload, variant): [] for workload in workloads for variant in variants}
    with open("results.tsv", "w") as results:
        results.write("round\tvariant\tworkload\tnanoseconds_per_item\n")
        for round_index in range(rounds):
            shift = round_index % len(variants)
            for workload in workloads:
                for variant in variants[shift:] + variants[:shift]:
                    ((name, value),) = run(binaries[variant], repetitions, workload)
                    if name != workload:
                        sys.exit(f"{variant} ran {name!r} instead of {workload!r}")
                    samples[(workload, variant)].append(float(value))
                    results.write(f"{round_index + 1}\t{variant}\t{workload}\t{value}\n")
            results.flush()
            print(f"round {round_index + 1}/{rounds}", file=sys.stderr, flush=True)

    baseline, others = variants[0], variants[1:]
    lines = [
        f"### {platform.system()} {platform.release()} ({platform.machine()}), "
        f"{os.cpu_count()} CPUs, {cpu_name()}",
        "",
        "Keyed inserts into a `SumTree`, release build, nanoseconds per inserted item. Every workload "
        f"runs in a process of its own and reports its median of {repetitions} repetitions; medians "
        f"over {rounds} rounds that rotate which variant goes first:",
        "",
        "| Workload | "
        + " | ".join(variants)
        + " | "
        + " | ".join(f"{variant} change | {variant} faster" for variant in others)
        + " |",
        "|---|" + "---|" * (len(variants) + 2 * len(others)),
    ]
    for workload in workloads:
        medians = {variant: statistics.median(samples[(workload, variant)]) for variant in variants}
        cells = [f"{medians[variant]:.1f}" for variant in variants]
        for variant in others:
            pairs = zip(samples[(workload, baseline)], samples[(workload, variant)])
            faster = sum(1 for old, new in pairs if new < old)
            cells += [f"{100 * (medians[variant] / medians[baseline] - 1):+.0f}%", f"{faster}/{rounds}"]
        lines.append(f"| {workload} | " + " | ".join(cells) + " |")

    summary = "\n".join(lines) + "\n"
    print(summary)
    with open(summary_path, "a", encoding="utf-8") as summary_file:
        summary_file.write(summary)


if __name__ == "__main__":
    main()
