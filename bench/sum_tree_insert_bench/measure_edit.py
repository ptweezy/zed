"""Usage: measure_edit.py <binaries-dir> <summary-path> <rounds> <repetitions>

Runs the `before` and `after` bench_edit binaries for <rounds> rounds,
swapping which goes first each round. Each process reports its median of
<repetitions> repetitions per workload, in nanoseconds per inserted item.
"""

import os
import platform
import statistics
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

SUFFIX = ".exe" if os.name == "nt" else ""
VARIANTS = ["before", "after"]


def measure(binary, repetitions):
    completed = subprocess.run([binary, str(repetitions)], capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        sys.exit(f"{binary} exited with {completed.returncode}:\n{completed.stdout}\n{completed.stderr}")
    return {name: float(value) for name, value in (line.split("\t") for line in completed.stdout.splitlines())}


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


def main():
    binaries_dir, summary_path, rounds, repetitions = sys.argv[1:]
    rounds, repetitions = int(rounds), int(repetitions)
    binaries = {variant: os.path.join(binaries_dir, variant + SUFFIX) for variant in VARIANTS}
    samples = {variant: [] for variant in VARIANTS}
    with open("results.tsv", "w") as results:
        results.write("round\tvariant\tworkload\tnanoseconds_per_item\n")
        for round_index in range(rounds):
            shift = round_index % len(VARIANTS)
            for variant in VARIANTS[shift:] + VARIANTS[:shift]:
                result = measure(binaries[variant], repetitions)
                samples[variant].append(result)
                for workload, value in result.items():
                    results.write(f"{round_index + 1}\t{variant}\t{workload}\t{value}\n")
            results.flush()
            print(f"round {round_index + 1}/{rounds}", file=sys.stderr, flush=True)

    lines = [
        f"### {platform.system()} {platform.release()} ({platform.machine()}), "
        f"{os.cpu_count()} CPUs, {cpu_name()}",
        "",
        "Keyed inserts into a `SumTree`, release build, nanoseconds per inserted item. Each process "
        f"reports its median of {repetitions} repetitions; medians over {rounds} rounds that swap "
        "which variant goes first:",
        "",
        "| Workload | before | after | Change | after faster |",
        "|---|---|---|---|---|",
    ]
    for workload in samples["before"][0]:
        before = [result[workload] for result in samples["before"]]
        after = [result[workload] for result in samples["after"]]
        faster = sum(1 for old, new in zip(before, after) if new < old)
        change = (statistics.median(after) / statistics.median(before) - 1) * 100
        lines.append(
            f"| {workload} | {statistics.median(before):.1f} | {statistics.median(after):.1f} | "
            f"{change:+.0f}% | {faster}/{rounds} |"
        )

    summary = "\n".join(lines) + "\n"
    print(summary)
    with open(summary_path, "a", encoding="utf-8") as summary_file:
        summary_file.write(summary)


if __name__ == "__main__":
    main()
