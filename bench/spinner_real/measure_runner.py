#!/usr/bin/env python3
"""Measures a release build of Zed on a macOS or Windows desktop while an agent thread waits.

Usage: measure_runner.py <runs> <scenario>[,<scenario>...]

Run from a zed checkout of the bench branch after `cargo build --release -p zed`.
Scenarios are the mock_openai.py modes. Variants rotate order on each run:
  before   ZED_BENCH_UNCAPPED=1: the labels render on every frame (main)
  after    the PR
  aligned  ZED_BENCH_ALIGNED=1: renders at glyph boundaries on a shared clock
Each run starts Zed with a fresh user data dir on a small project and
zed://agent/?prompt=hi, which the bench build submits to a local mock provider.
Once the thread reaches its state, the run waits SETTLE seconds, then samples
WINDOW seconds of Zed's process CPU time (psutil, all threads), frames drawn
(ZED_MEASUREMENTS=1 logs one line per frame), and compositor CPU time.
"""
import json, os, re, shutil, statistics, subprocess, sys, time

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
WINDOWS = sys.platform == "win32"
TARGET = os.environ.get("CARGO_TARGET_DIR", os.path.join(ROOT, "target"))
ZED = os.path.join(TARGET, "release", "zed.exe" if WINDOWS else "zed")
WORK = os.path.join(ROOT, "bench-work")
PROJECT = os.path.join(WORK, "project")
OPEN_FILE = os.path.join(PROJECT, "src", "window.rs")
RESULTS = os.path.join(WORK, "results")
PORTS = {"hang": 18701, "confirm": 18702, "done": 18703, "stream": 18704, "subagent": 18705, "subagents": 18706}
SETTLE = float(os.environ.get("BENCH_SETTLE", 5))
WINDOW = float(os.environ.get("BENCH_WINDOW", 10))
VARIANTS = os.environ.get("BENCH_VARIANTS", "before,after,aligned").split(",")
FRAME = re.compile(r"^frame duration: ([\d.]+)(ns|µs|ms|s)\r?$", re.M)
UNIT_MS = {"ns": 1e-6, "µs": 1e-3, "ms": 1.0, "s": 1e3}


def settings(port):
    return {
        "telemetry": {"diagnostics": False, "metrics": False},
        "auto_update": False,
        "auto_install_extensions": {"html": False},
        "enable_language_server": False,
        "session": {"trust_all_worktrees": True},
        "language_models": {
            "openai_compatible": {
                "bench": {
                    "api_url": f"http://127.0.0.1:{port}/v1",
                    "available_models": [{"name": "bench-model", "max_tokens": 128000}],
                }
            }
        },
        "agent": {"default_model": {"provider": "bench", "model": "bench-model"}},
    }


def screenshot(path):
    if WINDOWS:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
            "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($i);"
            "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
            f"$i.Save('{path}')"
        )
        command = ["powershell", "-NoProfile", "-Command", script]
    else:
        command = ["screencapture", "-x", path]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        print(f"screenshot failed: {result.stderr.strip()}", flush=True)


def compositor():
    name = "dwm.exe" if WINDOWS else "WindowServer"
    for process in psutil.process_iter(["name"]):
        if process.info["name"] == name:
            return process.pid
    return None


def compositor_cpu_seconds(pid):
    if pid is None:
        return None
    if WINDOWS:
        try:
            times = psutil.Process(pid).cpu_times()
            return times.user + times.system
        except psutil.Error:
            return None
    # psutil can't read WindowServer's times without root, but ps can.
    output = subprocess.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    if not output:
        return None
    seconds = 0.0
    for part in output.replace("-", ":").split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def frames(stderr_path):
    with open(stderr_path, encoding="utf-8", errors="replace") as file:
        return [float(value) * UNIT_MS[unit] for value, unit in FRAME.findall(file.read())]


def snapshot(zed, compositor_pid, stderr_path):
    times = zed.cpu_times()
    return {
        "time": time.monotonic(),
        "zed": times.user + times.system,
        "shell": compositor_cpu_seconds(compositor_pid),
        "frames": frames(stderr_path),
    }


def kill_tree(pid):
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    processes = parent.children(recursive=True) + [parent]
    for process in processes:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(processes, timeout=10)


def run_once(scenario, variant, compositor_pid, shot=None):
    run_dir = os.path.join(WORK, "runs", f"{scenario}-{variant}")
    shutil.rmtree(run_dir, ignore_errors=True)
    data = os.path.join(run_dir, "data")
    os.makedirs(os.path.join(data, "config"))
    with open(os.path.join(data, "config", "settings.json"), "w") as file:
        json.dump(settings(PORTS[scenario]), file)
    marker = os.path.join(WORK, f"marker-{scenario}")
    open(marker, "w").close()
    env = {key: value for key, value in os.environ.items() if not key.startswith("ZED_BENCH_")}
    env.update(BENCH_API_KEY="bench", ZED_BENCH_AUTO_SUBMIT="1", ZED_MEASUREMENTS="1")
    if variant == "before":
        env["ZED_BENCH_UNCAPPED"] = "1"
    elif variant == "aligned":
        env["ZED_BENCH_ALIGNED"] = "1"
    stderr_path = os.path.join(run_dir, "stderr.log")
    with open(stderr_path, "w") as stderr, open(os.path.join(run_dir, "stdout.log"), "w") as stdout:
        process = subprocess.Popen([ZED, "--user-data-dir", data, PROJECT, OPEN_FILE, "zed://agent/?prompt=hi"],
                                   env=env, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, cwd=run_dir)
    try:
        zed = psutil.Process(process.pid)
        deadline = time.time() + 120
        while os.path.getsize(marker) == 0:
            if process.poll() is not None:
                raise RuntimeError(f"zed exited with {process.returncode}, see {stderr_path}")
            if time.time() > deadline:
                raise RuntimeError(f"no agent request, see {stderr_path}")
            time.sleep(0.1)
        time.sleep(SETTLE)
        start = snapshot(zed, compositor_pid, stderr_path)
        time.sleep(WINDOW)
        end = snapshot(zed, compositor_pid, stderr_path)
        if shot:
            screenshot(shot)
    finally:
        kill_tree(process.pid)
    elapsed = end["time"] - start["time"]
    window_frames = end["frames"][len(start["frames"]):]
    shell = None
    if start["shell"] is not None and end["shell"] is not None:
        shell = (end["shell"] - start["shell"]) * 1e3 / elapsed
    return {
        "zed": (end["zed"] - start["zed"]) * 1e3 / elapsed,
        "shell": shell,
        "fps": len(window_frames) / elapsed,
        "frame_ms": statistics.mean(window_frames) if window_frames else 0.0,
    }


def main():
    # The Windows console encoding can't print the arrows in the summary.
    sys.stdout.reconfigure(encoding="utf-8")
    runs = int(sys.argv[1])
    scenarios = sys.argv[2].split(",")
    os.makedirs(os.path.join(PROJECT, "src"), exist_ok=True)
    shutil.copy(os.path.join(ROOT, "crates", "gpui", "src", "window.rs"), OPEN_FILE)
    with open(os.path.join(PROJECT, "README.md"), "w") as file:
        file.write("# Bench project\n")
    os.makedirs(RESULTS, exist_ok=True)
    raw = open(os.path.join(RESULTS, "runs.jsonl"), "w")
    mocks = [subprocess.Popen([sys.executable, os.path.join(HERE, "mock_openai.py"), str(port), mode,
                               os.path.join(WORK, f"marker-{mode}")],
                              stdout=subprocess.DEVNULL,
                              stderr=open(os.path.join(RESULTS, f"mock-{mode}.log"), "w"))
             for mode, port in PORTS.items()]
    compositor_pid = compositor()
    awake = None
    if sys.platform == "darwin":
        # A sleeping display stops frames for every window.
        subprocess.run(["sudo", "-n", "pmset", "-a", "displaysleep", "0", "sleep", "0"], capture_output=True)
        awake = subprocess.Popen(["caffeinate", "-dimsu"])
    rows = []
    try:
        time.sleep(1)
        run_once("hang", "after", compositor_pid, os.path.join(RESULTS, "warmup.png"))
        for scenario in scenarios:
            results = {variant: [] for variant in VARIANTS}
            for index in range(runs):
                shift = index % len(VARIANTS)
                for variant in VARIANTS[shift:] + VARIANTS[:shift]:
                    shot = os.path.join(RESULTS, f"{scenario}-{variant}.png") if index == 0 else None
                    try:
                        result = run_once(scenario, variant, compositor_pid, shot)
                    except RuntimeError as error:
                        print(f"retrying {scenario} {variant}: {error}", flush=True)
                        result = run_once(scenario, variant, compositor_pid, shot)
                    results[variant].append(result)
                    record = dict(scenario=scenario, variant=variant, index=index, **result)
                    raw.write(json.dumps(record) + "\n")
                    raw.flush()
                    print(json.dumps(record), flush=True)
            rows.append((scenario, results))
    finally:
        for mock in mocks:
            mock.kill()
        if awake:
            awake.kill()
    others = [variant for variant in VARIANTS if variant != "before"]
    print()
    print(f"Medians of {runs} runs per variant, rotating order; {WINDOW:.0f} s windows")
    print("| Scenario | Frames/s | Zed CPU ms/s | Compositor ms/s |")
    print("|---|---|---|---|")
    for scenario, results in rows:
        def cell(key):
            values = {variant: [result[key] for result in results[variant] if result[key] is not None]
                      for variant in VARIANTS}
            if not values["before"]:
                return "n/a"
            before = statistics.median(values["before"])
            parts = [f"{before:.1f}"]
            for variant in others:
                value = statistics.median(values[variant])
                change = f" ({100 * (value - before) / before:+.0f}%)" if before else ""
                parts.append(f"{value:.1f}{change}")
            return " → ".join(parts)

        print(f"| {scenario} | {cell('fps')} | {cell('zed')} | {cell('shell')} |")
    print(f"Cells: {' → '.join(VARIANTS)}")


if __name__ == "__main__":
    main()
