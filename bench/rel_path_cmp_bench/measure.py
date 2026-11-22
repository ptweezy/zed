"""Usage: measure.py <binaries-dir> <summary-path> <repetitions> <variant> ... -- <list>=<rounds> ...

Runs each variant's bench_rel_path_cmp binary on each path list. Each round
runs every variant once, rotating which goes first. The first variant is the
baseline.
"""

import os
import platform
import statistics
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

SUFFIX = ".exe" if os.name == "nt" else ""
LABELS = {
    "neighbours": "Sorted neighbours",
    "equal": "Equal paths",
    "random": "Random pairs",
    "search": "Binary search for every path",
    "sort": "Sorting all paths",
}


def measure(binary, path_list, repetitions):
    completed = subprocess.run(
        [binary, path_list, str(repetitions)], capture_output=True, text=True, encoding="utf-8"
    )
    if completed.returncode != 0:
        sys.exit(
            f"{binary} {path_list} exited with {completed.returncode}:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    result = {}
    for line in completed.stdout.splitlines():
        name, *fields = line.split("\t")
        if name == "paths":
            result["paths"] = (int(fields[0]), float(fields[1]), int(fields[2]))
        else:
            result[name] = {"unit": fields[0], "median": float(fields[1]), "minimum": float(fields[2])}
    return result


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
    arguments = sys.argv[1:]
    separator = arguments.index("--")
    binaries_dir, summary_path, repetitions, *variants = arguments[:separator]
    lists = []
    for argument in arguments[separator + 1 :]:
        path, rounds = argument.rsplit("=", 1)
        lists.append((os.path.abspath(path), int(rounds)))
    binaries = {variant: os.path.join(binaries_dir, variant + SUFFIX) for variant in variants}
    baseline, others = variants[0], variants[1:]

    lines = [
        f"### {platform.system()} {platform.release()} ({platform.machine()}), "
        f"{os.cpu_count()} CPUs, {cpu_name()}",
        "",
        "`RelPath::cmp` on paths from `git ls-tree -r -t`, release build. Each process "
        f"reports its median of {repetitions} repetitions; each round runs every variant "
        "once, rotating which goes first. Medians over the rounds:",
        "",
        "| Paths | Workload | "
        + " | ".join(variants)
        + " | "
        + " | ".join(f"{variant} change | {variant} faster" for variant in others)
        + " |",
        "|---|---|" + "---|" * (len(variants) + 2 * len(others)),
    ]
    with open("results.tsv", "w") as results:
        results.write("list\tround\tvariant\tworkload\tunit\tmedian\tminimum\n")
        for path_list, rounds in lists:
            name = os.path.splitext(os.path.basename(path_list))[0]
            for variant in variants:
                measure(binaries[variant], path_list, 1)
            samples = {variant: [] for variant in variants}
            for round_index in range(rounds):
                shift = round_index % len(variants)
                for variant in variants[shift:] + variants[:shift]:
                    result = measure(binaries[variant], path_list, repetitions)
                    samples[variant].append(result)
                    for workload in LABELS:
                        values = result[workload]
                        results.write(
                            f"{name}\t{round_index + 1}\t{variant}\t{workload}\t{values['unit']}\t"
                            f"{values['median']}\t{values['minimum']}\n"
                        )
                results.flush()
                print(f"{name}: round {round_index + 1}/{rounds}", file=sys.stderr, flush=True)

            descriptions = {result["paths"] for variant in variants for result in samples[variant]}
            if len(descriptions) != 1:
                sys.exit(f"{name}: the variants read different lists: {descriptions}")
            count, mean_bytes, skipped = descriptions.pop()
            label = f"{name}: {count:,} paths, mean {mean_bytes:.0f} bytes"
            if skipped:
                label += f", {skipped} skipped"
            for workload, title in LABELS.items():
                values = {
                    variant: [result[workload]["median"] for result in samples[variant]]
                    for variant in variants
                }
                unit = samples[baseline][0][workload]["unit"]
                medians = {variant: statistics.median(values[variant]) for variant in variants}
                comparisons = []
                for variant in others:
                    change = (medians[variant] / medians[baseline] - 1) * 100
                    faster = sum(
                        1 for base, other in zip(values[baseline], values[variant]) if other < base
                    )
                    comparisons.append(f"{change:+.1f}% | {faster}/{rounds}")
                lines.append(
                    f"| {label} | {title} | "
                    + " | ".join(f"{medians[variant]:.2f} {unit}" for variant in variants)
                    + " | "
                    + " | ".join(comparisons)
                    + " |"
                )

    summary = "\n".join(lines) + "\n"
    print(summary)
    with open(summary_path, "a", encoding="utf-8") as summary_file:
        summary_file.write(summary)


if __name__ == "__main__":
    main()
