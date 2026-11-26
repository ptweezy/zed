"""Usage: measure_scan.py <binaries-dir> <summary-path> <variant> ... -- <tree>=<runs> ...

Runs each variant's worktree-benchmarks binary on each tree, one scan per
process, after one warm-up scan per variant. Each round runs every variant
once, rotating which goes first. The first variant is the baseline.

Scan time is the wall time the binary prints. CPU time is the process's user
and system time, summed over its threads.
"""

import os
import platform
import re
import statistics
import subprocess
import sys

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
        return tuple(
            (filetime.dwHighDateTime << 32 | filetime.dwLowDateTime) / 1e4
            for filetime in (user, kernel)
        )

else:
    import resource

    def children_cpu_milliseconds():
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        return usage.ru_utime * 1e3, usage.ru_stime * 1e3


def scan(binary, tree):
    if os.name != "nt":
        user_before, system_before = children_cpu_milliseconds()
    process = subprocess.Popen([binary, tree], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    if os.name == "nt":
        user, system = process_cpu_milliseconds(process)
    else:
        user_after, system_after = children_cpu_milliseconds()
        user, system = user_after - user_before, system_after - system_before
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
                "user": user,
                "system": system,
                "cpu": user + system,
                "entries": int(match[3]) + int(match[4]),
            }
    sys.exit(f"no scan time in the output of {binary} {tree}:\n{output}")


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
        results.write("tree\tround\tvariant\tscan_ms\tuser_ms\tsystem_ms\tentries\n")
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
                        f"{result['user']:.3f}\t{result['system']:.3f}\t{result['entries']}\n"
                    )
                results.flush()
                print(f"{name}: round {round_index + 1}/{runs}", file=sys.stderr, flush=True)

    def columns(title, lower):
        return (
            " | ".join(f"{title} {variant}" for variant in variants)
            + " | "
            + " | ".join(f"{title} change | {variant} {lower}" for variant in others)
        )

    lines = [
        f"### {platform.system()} {platform.release()} ({platform.machine()}), "
        f"{os.cpu_count()} CPUs, {cpu_name()}",
        "",
        "Initial scan with `Worktree::local` and `RealFs` in a headless app, release build, "
        "one scan per process, after one warm-up scan per variant. Each round runs every "
        "variant, rotating which goes first. Medians:",
        "",
        f"| Tree | Entries | Runs | {columns('Scan', 'faster')} | {columns('CPU', 'lower')} | Same entries |",
        "|---|---|---|" + "---|" * (2 * (len(variants) + 2 * len(others))) + "---|",
    ]
    for tree, runs in trees:
        name = os.path.basename(tree)
        entries = sorted({result["entries"] for variant in variants for result in samples[(name, variant)]})
        cells = []
        for key in ("scan", "cpu"):
            values = {variant: [result[key] for result in samples[(name, variant)]] for variant in variants}
            medians = {variant: statistics.median(values[variant]) for variant in variants}
            cells += [format_duration(medians[variant]) for variant in variants]
            for variant in others:
                change = (medians[variant] / medians[baseline] - 1) * 100
                lower = sum(1 for base, other in zip(values[baseline], values[variant]) if other < base)
                cells += [f"{change:+.1f}%", f"{lower}/{runs}"]
        lines.append(
            f"| {name} | {' to '.join(f'{count:,}' for count in entries)} | {runs} | "
            + " | ".join(cells)
            + f" | {'yes' if len(entries) == 1 else 'NO'} |"
        )

    summary = "\n".join(lines) + "\n"
    print(summary)
    with open(summary_path, "a", encoding="utf-8") as summary_file:
        summary_file.write(summary)


if __name__ == "__main__":
    main()
