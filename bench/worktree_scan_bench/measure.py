"""Usage: measure.py <binaries-dir> <summary-path> <variant> ... -- <tree>=<runs> ...

Runs each variant's worktree-benchmarks binary on each tree, one scan per
process, after one warm-up scan per variant. Each round runs every variant
once, rotating which goes first. The first variant is the baseline.
"""

import os
import platform
import re
import statistics
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

DURATION = re.compile(r"^([0-9.]+)(ns|µs|ms|s) for (\d+) directories and (\d+) files$")
MILLISECONDS_PER_UNIT = {"ns": 1e-6, "µs": 1e-3, "ms": 1.0, "s": 1e3}
SUFFIX = ".exe" if os.name == "nt" else ""

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    def process_cpu_milliseconds(process):
        creation, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not ctypes.windll.kernel32.GetProcessTimes(
            wintypes.HANDLE(int(process._handle)),
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise ctypes.WinError()
        return sum(
            (filetime.dwHighDateTime << 32 | filetime.dwLowDateTime) / 1e4
            for filetime in (kernel, user)
        )

else:
    import resource

    def children_cpu_milliseconds():
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        return (usage.ru_utime + usage.ru_stime) * 1e3


def scan(binary, tree):
    if os.name != "nt":
        cpu_before = children_cpu_milliseconds()
    start = time.perf_counter()
    process = subprocess.Popen([binary, tree], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    wall = (time.perf_counter() - start) * 1e3
    if os.name == "nt":
        cpu = process_cpu_milliseconds(process)
    else:
        cpu = children_cpu_milliseconds() - cpu_before
    output = stdout.decode("utf-8", "replace")
    if process.returncode != 0:
        sys.exit(
            f"{binary} {tree} exited with {process.returncode}:\n{output}\n"
            + stderr.decode("utf-8", "replace")
        )
    for line in output.splitlines():
        match = DURATION.match(line.strip())
        if match:
            return {
                "scan": float(match[1]) * MILLISECONDS_PER_UNIT[match[2]],
                "wall": wall,
                "cpu": cpu,
                "entries": int(match[3]) + int(match[4]),
            }
    sys.exit(f"no scan time in the output of {binary} {tree}:\n{output}")


def cpu_name():
    try:
        if sys.platform == "linux":
            with open("/proc/cpuinfo") as cpuinfo:
                for line in cpuinfo:
                    if line.startswith("model name"):
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


def format_duration(milliseconds):
    if milliseconds >= 1000:
        return f"{milliseconds / 1000:.2f} s"
    if milliseconds >= 10:
        return f"{milliseconds:.1f} ms"
    return f"{milliseconds:.2f} ms"


def main():
    arguments = sys.argv[1:]
    separator = arguments.index("--")
    binaries_dir, summary_path, *variants = arguments[:separator]
    trees = []
    for argument in arguments[separator + 1 :]:
        path, runs = argument.rsplit("=", 1)
        trees.append((os.path.abspath(path), int(runs)))
    binaries = {variant: os.path.join(binaries_dir, variant + SUFFIX) for variant in variants}
    baseline, others = variants[0], variants[1:]

    samples = {}
    with open("results.tsv", "w") as results:
        results.write("tree\tround\tvariant\tscan_ms\twall_ms\tcpu_ms\tentries\n")
        for tree, runs in trees:
            name = os.path.basename(tree)
            for variant in variants:
                scan(binaries[variant], tree)
            for round_index in range(runs):
                shift = round_index % len(variants)
                for variant in variants[shift:] + variants[:shift]:
                    result = scan(binaries[variant], tree)
                    samples.setdefault((name, variant), []).append(result)
                    results.write(
                        f"{name}\t{round_index + 1}\t{variant}\t{result['scan']:.3f}\t"
                        f"{result['wall']:.3f}\t{result['cpu']:.3f}\t{result['entries']}\n"
                    )
                results.flush()
                print(f"{name}: round {round_index + 1}/{runs}", file=sys.stderr, flush=True)

    lines = [
        f"### {platform.system()} {platform.release()} ({platform.machine()}), "
        f"{os.cpu_count()} CPUs, {cpu_name()}",
        "",
        "Initial scan with `Worktree::local` and `RealFs` in a headless app, release build, "
        "one scan per process, after one warm-up scan per variant. Each round runs every "
        "variant, rotating which goes first. Medians:",
        "",
        "| Tree | Entries | Runs | "
        + " | ".join(variants)
        + " | "
        + " | ".join(f"{variant} change | {variant} faster" for variant in others)
        + " | "
        + " | ".join(f"CPU {variant}" for variant in variants)
        + " | Same entries |",
        "|---|---|---|" + "---|" * (len(variants) + 2 * len(others) + len(variants)) + "---|",
    ]
    details = ["", "<details><summary>Every run, in round order</summary>", ""]
    for tree, runs in trees:
        name = os.path.basename(tree)
        entries = sorted({result["entries"] for variant in variants for result in samples[(name, variant)]})
        scans = {variant: [result["scan"] for result in samples[(name, variant)]] for variant in variants}
        cpus = {variant: [result["cpu"] for result in samples[(name, variant)]] for variant in variants}
        medians = {variant: statistics.median(scans[variant]) for variant in variants}
        comparisons = []
        for variant in others:
            change = (medians[variant] / medians[baseline] - 1) * 100
            faster = sum(1 for base, other in zip(scans[baseline], scans[variant]) if other < base)
            comparisons.append(f"{change:+.1f}% | {faster}/{runs}")
        lines.append(
            f"| {name} | {' to '.join(f'{count:,}' for count in entries)} | {runs} | "
            + " | ".join(format_duration(medians[variant]) for variant in variants)
            + " | "
            + " | ".join(comparisons)
            + " | "
            + " | ".join(format_duration(statistics.median(cpus[variant])) for variant in variants)
            + f" | {'yes' if len(entries) == 1 else 'NO'} |"
        )
        for variant in variants:
            details.append(
                f"- {name}, {variant}: " + ", ".join(format_duration(value) for value in scans[variant])
            )
    details += ["", "</details>"]

    summary = "\n".join(lines + details) + "\n"
    print(summary)
    with open(summary_path, "a", encoding="utf-8") as summary_file:
        summary_file.write(summary)


if __name__ == "__main__":
    main()
