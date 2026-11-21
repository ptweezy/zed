#!/usr/bin/env python3
"""Measures a release build of Zed on Linux while an agent thread waits.

Usage: measure_real.py <runs> <hz>[,<hz>...] <scenario>[,<scenario>...]

Scenarios: the mock_openai.py modes (hang, confirm, done, stream, subagent,
subagents), plus hidden: hang with a fullscreen window covering Zed.

For each refresh rate, this starts a headless GNOME Shell whose virtual monitor
runs at that rate, then rotates the order of three variants of one bench build:
  before   ZED_BENCH_UNCAPPED=1: the labels render on every frame (main)
  after    the PR
  aligned  ZED_BENCH_ALIGNED=1: renders at glyph boundaries on a shared clock
BENCH_VARIANTS picks a subset. Each run starts Zed with a fresh user data dir on
a small project and zed://agent/?prompt=hi, which the bench build submits to a
local mock provider. Once the thread reaches its state, the run waits SETTLE
seconds, then samples WINDOW seconds of:
  zed    CPU time of the Zed process, all threads (/proc/<pid>/stat)
  main   CPU time of Zed's main thread (/proc/<pid>/task/<pid>/schedstat)
  shell  CPU time of the compositor process
  frames frames Zed drew (ZED_MEASUREMENTS=1 logs one line per frame)
BENCH_ZED and BENCH_WORK override the binary (target/release/zed) and the
scratch dir (bench-work in the checkout).
"""
import json, os, re, shutil, signal, statistics, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
TARGET = os.environ.get("CARGO_TARGET_DIR", os.path.join(ROOT, "target"))
ZED = os.environ.get("BENCH_ZED", os.path.join(TARGET, "release", "zed"))
WORK = os.environ.get("BENCH_WORK", os.path.join(ROOT, "bench-work"))
RUNTIME = os.environ["XDG_RUNTIME_DIR"]
DISPLAY_NAME = "wayland-bench"
TICKS_PER_SECOND = os.sysconf("SC_CLK_TCK")
PORTS = {"hang": 18701, "confirm": 18702, "done": 18703, "stream": 18704, "subagent": 18705, "subagents": 18706}
MODES = {"hidden": "hang"}
PROJECT = os.path.join(WORK, "bench-project")
OPEN_FILE = os.path.join(PROJECT, "src", "window.rs")
SETTLE = float(os.environ.get("BENCH_SETTLE", 5))
WINDOW = float(os.environ.get("BENCH_WINDOW", 10))
BASE_ENV = {
    "HOME": os.environ["HOME"],
    "USER": os.environ["USER"],
    "LOGNAME": os.environ["USER"],
    "PATH": "/usr/bin:/bin",
    "LANG": os.environ.get("LANG", "C.UTF-8"),
    "XDG_RUNTIME_DIR": RUNTIME,
}
FRAME = re.compile(r"^frame duration: ([\d.]+)(ns|µs|ms|s)$", re.M)
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


def zed_env(run_dir, variant="after"):
    home = os.path.join(WORK, "zed-home")
    os.makedirs(home, exist_ok=True)
    env = dict(
        BASE_ENV,
        HOME=home,
        WAYLAND_DISPLAY=DISPLAY_NAME,
        XDG_CONFIG_HOME=f"{run_dir}/xdg-config",
        XDG_DATA_HOME=f"{run_dir}/xdg-data",
        XDG_STATE_HOME=f"{run_dir}/xdg-state",
        XDG_CACHE_HOME=f"{WORK}/zed-cache",
        BENCH_API_KEY="bench",
        ZED_BENCH_AUTO_SUBMIT="1",
        ZED_MEASUREMENTS="1",
    )
    if variant == "before":
        env["ZED_BENCH_UNCAPPED"] = "1"
    elif variant == "aligned":
        env["ZED_BENCH_ALIGNED"] = "1"
    return env


def shell_bus_address(shell_pid):
    with open(f"/proc/{shell_pid}/environ", "rb") as file:
        for entry in file.read().split(b"\0"):
            if entry.startswith(b"DBUS_SESSION_BUS_ADDRESS="):
                return entry.split(b"=", 1)[1].decode()
    raise RuntimeError("no bus address")


def start_shell(hz):
    socket = os.path.join(RUNTIME, DISPLAY_NAME)
    pattern = f"^gnome-shell --headless.*{DISPLAY_NAME}"

    def running():
        return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0

    subprocess.run(["pkill", "-f", pattern])
    deadline = time.time() + 15
    while running() and time.time() < deadline:
        time.sleep(0.2)
    if running():
        subprocess.run(["pkill", "-9", "-f", pattern])
        time.sleep(1)
    for leftover in (socket, socket + ".lock"):
        if os.path.exists(leftover):
            os.remove(leftover)
    home = os.path.join(WORK, "gs-home")
    env = dict(
        BASE_ENV,
        XDG_CONFIG_HOME=f"{home}/config",
        XDG_DATA_HOME=f"{home}/data",
        XDG_CACHE_HOME=f"{home}/cache",
        GSETTINGS_BACKEND="memory",
    )
    log = open(os.path.join(WORK, f"gs-{hz}.log"), "w")
    process = subprocess.Popen(
        ["dbus-run-session", "--", "gnome-shell", "--headless", "--wayland", "--no-x11",
         f"--wayland-display={DISPLAY_NAME}", "--virtual-monitor", f"1920x1080@{hz}"],
        env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    deadline = time.time() + 30
    shell_pid = None
    while shell_pid is None or not os.path.exists(socket):
        if time.time() > deadline or process.poll() is not None:
            sys.exit("gnome-shell did not start")
        children = subprocess.run(["pgrep", "-P", str(process.pid), "-x", "gnome-shell"],
                                  capture_output=True, text=True).stdout.split()
        shell_pid = int(children[0]) if children else None
        time.sleep(0.2)
    time.sleep(3)
    subprocess.run(["gdbus", "call", "--address", shell_bus_address(shell_pid),
                    "--dest", "org.gnome.Shell", "--object-path", "/org/gnome/Shell",
                    "--method", "org.freedesktop.DBus.Properties.Set",
                    "org.gnome.Shell", "OverviewActive", "<false>"],
                   check=True, capture_output=True)
    time.sleep(2)
    return process, shell_pid


def stop_group(process, timeout=5):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def process_cpu_seconds(pid):
    with open(f"/proc/{pid}/stat") as file:
        fields = file.read().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / TICKS_PER_SECOND


def thread_cpu_seconds(pid, tid):
    with open(f"/proc/{pid}/task/{tid}/schedstat") as file:
        return int(file.read().split()[0]) / 1e9


def frames(stderr_path):
    with open(stderr_path, encoding="utf-8", errors="replace") as file:
        return [float(value) * UNIT_MS[unit] for value, unit in FRAME.findall(file.read())]


def snapshot(pid, shell_pid, stderr_path):
    return {
        "time": time.monotonic(),
        "zed": process_cpu_seconds(pid),
        "main": thread_cpu_seconds(pid, pid),
        "shell": process_cpu_seconds(shell_pid),
        "frames": frames(stderr_path),
    }


def start_occluder(shell_pid):
    env = dict(BASE_ENV, WAYLAND_DISPLAY=DISPLAY_NAME,
               DBUS_SESSION_BUS_ADDRESS=shell_bus_address(shell_pid))
    return subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "occluder.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def run_once(scenario, variant, shell_pid, screenshot=None):
    mode = MODES.get(scenario, scenario)
    run_dir = os.path.join(WORK, "runs", f"{scenario}-{variant}")
    shutil.rmtree(run_dir, ignore_errors=True)
    data = os.path.join(run_dir, "data")
    os.makedirs(os.path.join(data, "config"))
    with open(os.path.join(data, "config", "settings.json"), "w") as file:
        json.dump(settings(PORTS[mode]), file)
    marker = os.path.join(WORK, f"marker-{mode}")
    open(marker, "w").close()
    env = zed_env(run_dir, variant)
    stderr_path = os.path.join(run_dir, "stderr.log")
    with open(stderr_path, "w") as stderr, open(os.path.join(run_dir, "stdout.log"), "w") as stdout:
        zed = subprocess.Popen([ZED, "--user-data-dir", data, PROJECT, OPEN_FILE, "zed://agent/?prompt=hi"],
                               env=env, stdout=stdout, stderr=stderr, cwd=run_dir,
                               start_new_session=True)
    occluder = None
    try:
        deadline = time.time() + 60
        while os.path.getsize(marker) == 0:
            if zed.poll() is not None:
                raise RuntimeError(f"zed exited with {zed.returncode}, see {stderr_path}")
            if time.time() > deadline:
                raise RuntimeError(f"no agent request, see {stderr_path}")
            time.sleep(0.1)
        if scenario == "hidden":
            occluder = start_occluder(shell_pid)
        time.sleep(SETTLE)
        start = snapshot(zed.pid, shell_pid, stderr_path)
        time.sleep(WINDOW)
        end = snapshot(zed.pid, shell_pid, stderr_path)
        if screenshot:
            subprocess.run([sys.executable, "-I", os.path.join(HERE, "screenshot.py"),
                            str(shell_pid), screenshot], check=True, capture_output=True)
    finally:
        stop_group(zed)
        if occluder:
            stop_group(occluder)
    elapsed = end["time"] - start["time"]
    window_frames = end["frames"][len(start["frames"]):]
    return {
        "zed": (end["zed"] - start["zed"]) * 1e3 / elapsed,
        "main": (end["main"] - start["main"]) * 1e3 / elapsed,
        "shell": (end["shell"] - start["shell"]) * 1e3 / elapsed,
        "fps": len(window_frames) / elapsed,
        "frame_ms": statistics.mean(window_frames) if window_frames else 0.0,
    }


def main():
    runs = int(sys.argv[1])
    rates = [int(rate) for rate in sys.argv[2].split(",")]
    scenarios = sys.argv[3].split(",")
    os.makedirs(os.path.join(WORK, "results"), exist_ok=True)
    if not os.path.exists(OPEN_FILE):
        os.makedirs(os.path.dirname(OPEN_FILE), exist_ok=True)
        shutil.copy(os.path.join(ROOT, "crates", "gpui", "src", "window.rs"), OPEN_FILE)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    raw = open(os.path.join(WORK, "results", f"{stamp}.jsonl"), "w")
    mocks = [subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "mock_openai.py"),
                               str(port), mode, os.path.join(WORK, f"marker-{mode}")],
                              stderr=open(os.path.join(WORK, f"mock-{mode}.log"), "w"))
             for mode, port in PORTS.items()]
    variants = os.environ.get("BENCH_VARIANTS", "before,after,aligned").split(",")
    rows = []
    try:
        time.sleep(1)
        for hz in rates:
            shell, shell_pid = start_shell(hz)
            try:
                run_once("hang", "after", shell_pid)  # warm caches
                for scenario in scenarios:
                    results = {variant: [] for variant in variants}
                    for index in range(runs):
                        shift = index % len(variants)
                        for variant in variants[shift:] + variants[:shift]:
                            shot = None
                            if index == 0:
                                shot = os.path.join(WORK, "results", f"{stamp}-{hz}-{scenario}-{variant}.png")
                            try:
                                result = run_once(scenario, variant, shell_pid, shot)
                            except RuntimeError as error:
                                print(f"retrying {hz} {scenario} {variant}: {error}", flush=True)
                                result = run_once(scenario, variant, shell_pid, shot)
                            results[variant].append(result)
                            record = dict(hz=hz, scenario=scenario, variant=variant, index=index, **result)
                            raw.write(json.dumps(record) + "\n")
                            raw.flush()
                            print(json.dumps(record), flush=True)
                    rows.append((hz, scenario, results))
            finally:
                stop_group(shell)
    finally:
        for mock in mocks:
            mock.kill()
    print()
    print(f"Medians of {runs} runs per variant, rotating order; {WINDOW:.0f} s windows; load {open('/proc/loadavg').read().split()[:3]}")
    others = [variant for variant in variants if variant != "before"]
    columns = ["Frames/s", "Zed CPU ms/s", "Main thread ms/s", "Compositor ms/s"]
    print("| Display | Scenario | " + " | ".join(columns) + " |")
    print("|---|---|" + "---|" * len(columns))
    for hz, scenario, results in rows:
        def median(variant, key):
            return statistics.median(result[key] for result in results[variant])

        def cell(key):
            before = median("before", key)
            parts = [f"{before:.1f}"]
            for variant in others:
                value = median(variant, key)
                change = f" ({100 * (value - before) / before:+.0f}%)" if before else ""
                parts.append(f"{value:.1f}{change}")
            return " → ".join(parts)

        print(f"| {hz} Hz | {scenario} | {cell('fps')} | {cell('zed')} | {cell('main')} | {cell('shell')} |")
    print(f"Cells: {' → '.join(variants)}")


if __name__ == "__main__":
    main()
