#!/usr/bin/env python3
"""Measures a release build of Zed on a Windows desktop while it idles or redraws.

Usage: measure.py <runs> <scenario>[,<scenario>...]

Compares two builds, rotating their order on each run:
  before   BENCH_BIN/before/zed.exe: the vsync thread invalidates every window on every vblank
  after    BENCH_BIN/after/zed.exe: the vsync thread blocks until a window requests a frame

Each run starts Zed with a fresh user data dir on a small project, sets up the scenario,
waits SETTLE seconds, then samples a window (IDLE_WINDOW seconds for the idle scenarios,
WINDOW seconds for the others) of:
  - Zed's CPU time and context switches (psutil, all threads)
  - frames drawn (ZED_MEASUREMENTS=1 logs one line per drawn frame)
  - CPU time and context switches of the UI thread and of the `VSyncProvider` thread
  - dwm.exe CPU time

Idle scenarios:
  focused      the window has the foreground and the cursor blinks
  unfocused    another window has the foreground
  minimized    the window is minimized
  two-windows  a second window is open and has the foreground; the first one is minimized
Other scenarios:
  typing       10 keystrokes per second into a short text file
  wheel        80 mouse wheel notches per second over a long file, which redraws on every vblank
  latency      the time from a key press to the frame it causes, in a short text file with
               the cursor not blinking, for 40 presses 0.3 s apart

Before the scenarios, one probe run per build checks that Zed still draws frames after
launch, typing, a restore from minimized, and a resize, and saves a screenshot of each.
"""
import ctypes, json, os, re, shutil, statistics, subprocess, sys, threading, time
from ctypes import wintypes

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
BIN = os.environ.get("BENCH_BIN", r"C:\zed-bin")
WORK = os.path.join(ROOT, "bench-work")
PROJECT = os.path.join(WORK, "project")
LONG_FILE = os.path.join(PROJECT, "src", "window.rs")
SHORT_FILE = os.path.join(PROJECT, "notes.txt")
RESULTS = os.path.join(WORK, "results")
STARTUP = float(os.environ.get("BENCH_STARTUP", 10))
SETTLE = float(os.environ.get("BENCH_SETTLE", 5))
WINDOW = float(os.environ.get("BENCH_WINDOW", 10))
IDLE_WINDOW = float(os.environ.get("BENCH_IDLE_WINDOW", 20))
VARIANTS = ["before", "after"]
FRAME = re.compile(r"^frame duration: ", re.M)
SETTINGS = {
    "telemetry": {"diagnostics": False, "metrics": False},
    "auto_update": False,
    "auto_install_extensions": {"html": False},
    "enable_language_server": False,
    "session": {"trust_all_worktrees": True},
    "cursor_blink": True,
}
OCCLUDER_TITLE = "Bench occluder"
OCCLUDER = f"""
import tkinter
root = tkinter.Tk()
root.title({OCCLUDER_TITLE!r})
root.geometry("320x200+40+40")
root.attributes("-topmost", True)
root.mainloop()
"""

SW_SHOWNORMAL, SW_MINIMIZE, SW_RESTORE = 1, 6, 9
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_WHEEL = 0x0002, 0x0004, 0x0800
VK_SHIFT, VK_CONTROL, VK_J, VK_N = 0x10, 0x11, 0x4A, 0x4E
THREAD_QUERY_LIMITED_INFORMATION = 0x0800

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUM_WINDOWS_PROC, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_size_t]
user32.mouse_event.argtypes = [wintypes.DWORD] * 4 + [ctypes.c_size_t]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenThread.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
if hasattr(kernel32, "GetThreadDescription"):
    kernel32.GetThreadDescription.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.LPWSTR)]
    kernel32.GetThreadDescription.restype = ctypes.c_long


def class_name(hwnd):
    name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, name, 64)
    return name.value


def zed_windows(pid):
    """Top-level Zed windows of the process, minimized ones included, topmost first."""
    found = []

    @ENUM_WINDOWS_PROC
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and class_name(hwnd) == "Zed::Window":
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found


def is_foreground(hwnd):
    return user32.GetForegroundWindow() == hwnd


def foreground(hwnd):
    """Gives `hwnd` the foreground. A background process may only do that while its
    input is attached to the foreground thread's."""
    current = user32.GetForegroundWindow()
    if current != hwnd:
        other = user32.GetWindowThreadProcessId(current, None) if current else 0
        this = kernel32.GetCurrentThreadId()
        if other:
            user32.AttachThreadInput(this, other, True)
        user32.SetForegroundWindow(hwnd)
        if other:
            user32.AttachThreadInput(this, other, False)
        time.sleep(0.5)
    return is_foreground(hwnd)


def press(*keys):
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
    for key in reversed(keys):
        user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)


def click(x, y):
    user32.SetCursorPos(x, y)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def repeat(action, rate, stop):
    """Calls `action(count)` `rate` times per second until `stop` is set."""
    start = time.perf_counter()
    count = 0
    while not stop.is_set():
        action(count)
        count += 1
        delay = start + count / rate - time.perf_counter()
        if delay > 0:
            time.sleep(delay)


def thread_name(thread_id):
    if not hasattr(kernel32, "GetThreadDescription"):
        return None
    handle = kernel32.OpenThread(THREAD_QUERY_LIMITED_INFORMATION, False, thread_id)
    if not handle:
        return None
    try:
        name = wintypes.LPWSTR()
        if kernel32.GetThreadDescription(handle, ctypes.byref(name)) < 0:
            return None
        return name.value
    finally:
        kernel32.CloseHandle(handle)


def thread_switches(pid):
    """Context switch counters per thread, as {thread id: (count, seconds)}."""
    script = (
        "Get-CimInstance Win32_PerfRawData_PerfProc_Thread -Filter 'IDProcess=PID' | "
        "ForEach-Object { '{0} {1} {2} {3}' -f $_.IDThread, $_.ContextSwitchesPersec, "
        "$_.Timestamp_PerfTime, $_.Frequency_PerfTime }"
    ).replace("PID", str(pid))
    try:
        output = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError) as error:
        print(f"thread counters failed: {error}", flush=True)
        return {}
    counters = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 4 and all(field.isdigit() for field in fields) and int(fields[3]):
            counters[int(fields[0])] = (int(fields[1]), int(fields[2]) / int(fields[3]))
    return counters


def screenshot(path):
    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($i);"
        "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
        f"$i.Save('{path}')"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    if result.returncode:
        print(f"screenshot failed: {result.stderr.strip()}", flush=True)


def dwm_pid():
    for process in psutil.process_iter(["name"]):
        if process.info["name"] == "dwm.exe":
            return process.pid
    return None


def cpu_seconds(pid):
    if pid is None:
        return None
    try:
        times = psutil.Process(pid).cpu_times()
        return times.user + times.system
    except psutil.Error:
        return None


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


class Zed:
    """A running Zed with a fresh user data dir, killed on exit."""

    def __init__(self, variant, name, file=LONG_FILE, **settings):
        self.dir = os.path.join(WORK, "runs", f"{name}-{variant}")
        shutil.rmtree(self.dir, ignore_errors=True)
        data = os.path.join(self.dir, "data")
        os.makedirs(os.path.join(data, "config"), exist_ok=True)
        with open(os.path.join(data, "config", "settings.json"), "w") as settings_file:
            json.dump({**SETTINGS, **settings}, settings_file)
        self.stderr_path = os.path.join(self.dir, "stderr.log")
        env = dict(os.environ, ZED_MEASUREMENTS="1")
        with open(self.stderr_path, "w") as stderr, open(os.path.join(self.dir, "stdout.log"), "w") as stdout:
            self.process = subprocess.Popen(
                [os.path.join(BIN, variant, "zed.exe"), "--user-data-dir", data, PROJECT, file],
                env=env, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, cwd=self.dir)
        self.pid = self.process.pid

    def __enter__(self):
        try:
            self.zed = psutil.Process(self.pid)
            self.hwnd = self.wait_for_windows(1)[0]
            self.ui_thread = user32.GetWindowThreadProcessId(self.hwnd, None)
            time.sleep(STARTUP)
        except BaseException:
            # A Zed left behind would swallow the next launch as a second instance.
            kill_tree(self.pid)
            raise
        return self

    def __exit__(self, *_):
        kill_tree(self.pid)

    def wait_for_windows(self, count, timeout=120):
        deadline = time.time() + timeout
        while True:
            windows = zed_windows(self.pid)
            if len(windows) >= count:
                return windows
            if self.process.poll() is not None:
                raise RuntimeError(f"zed exited with {self.process.returncode}, see {self.stderr_path}")
            if time.time() > deadline:
                raise RuntimeError(f"{len(windows)} of {count} windows, see {self.stderr_path}")
            time.sleep(0.1)

    def frames(self):
        with open(self.stderr_path, encoding="utf-8", errors="replace") as file:
            return len(FRAME.findall(file.read()))

    def vsync_thread(self):
        for thread in self.zed.threads():
            if thread_name(thread.id) == "VSyncProvider":
                return thread.id
        return None

    def snapshot(self, compositor):
        times = self.zed.cpu_times()
        return {
            "time": time.monotonic(),
            "cpu": times.user + times.system,
            "switches": sum(self.zed.num_ctx_switches()),
            "threads": {thread.id: thread.user_time + thread.system_time for thread in self.zed.threads()},
            "dwm": cpu_seconds(compositor),
            "frames": self.frames(),
        }


def setup_focused(zed):
    foreground(zed.hwnd)


def setup_unfocused(zed):
    """Opens a small window of another process and clicks it, as a user would."""
    occluder = subprocess.Popen([sys.executable, "-c", OCCLUDER])
    try:
        deadline = time.time() + 30
        while not (hwnd := user32.FindWindowW(None, OCCLUDER_TITLE)):
            if occluder.poll() is not None or time.time() > deadline:
                raise RuntimeError("the occluder window did not open")
            time.sleep(0.1)
        time.sleep(1)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        time.sleep(0.5)
        if is_foreground(zed.hwnd):
            foreground(hwnd)
    except BaseException:
        occluder.kill()
        raise
    return occluder.kill


def setup_minimized(zed):
    user32.ShowWindow(zed.hwnd, SW_MINIMIZE)


def setup_two_windows(zed):
    foreground(zed.hwnd)
    press(VK_CONTROL, VK_SHIFT, VK_N)
    zed.wait_for_windows(2, timeout=30)
    time.sleep(2)
    user32.ShowWindow(zed.hwnd, SW_MINIMIZE)


def setup_wheel(zed):
    foreground(zed.hwnd)
    user32.SetCursorPos(400, 400)


def type_letter(_):
    press(VK_J)


def turn_wheel(count):
    # Five seconds down, then five seconds up, so the file never runs out.
    notch = -120 if (count // 400) % 2 == 0 else 120
    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, notch & 0xFFFFFFFF, 0)


SCENARIOS = {
    "focused": dict(setup=setup_focused, window=IDLE_WINDOW),
    "unfocused": dict(setup=setup_unfocused, window=IDLE_WINDOW),
    "minimized": dict(setup=setup_minimized, window=IDLE_WINDOW),
    "two-windows": dict(setup=setup_two_windows, window=IDLE_WINDOW),
    "typing": dict(setup=setup_focused, window=WINDOW, action=type_letter, rate=10, file=SHORT_FILE),
    "wheel": dict(setup=setup_wheel, window=WINDOW, action=turn_wheel, rate=80),
}


def rate(start, end, key):
    if start.get(key) is None or end.get(key) is None:
        return None
    return (end[key] - start[key]) / (end["time"] - start["time"])


def run_once(scenario, variant, compositor, shot=None):
    spec = SCENARIOS[scenario]
    with Zed(variant, scenario, file=spec.get("file", LONG_FILE)) as zed:
        cleanup = spec["setup"](zed)
        stop = threading.Event()
        actor = None
        if "action" in spec:
            actor = threading.Thread(target=repeat, args=(spec["action"], spec["rate"], stop))
            actor.start()
        try:
            time.sleep(SETTLE)
            vsync_thread = zed.vsync_thread()
            counters_start = thread_switches(zed.pid)
            start = zed.snapshot(compositor)
            time.sleep(spec["window"])
            end = zed.snapshot(compositor)
            counters_end = thread_switches(zed.pid)
            names = {thread_id: thread_name(thread_id) for thread_id in counters_end}
            windows = zed_windows(zed.pid)
            state = {
                "windows": len(windows),
                "minimized": sum(1 for hwnd in windows if user32.IsIconic(hwnd)),
                "foreground": any(is_foreground(hwnd) for hwnd in windows),
                "foreground_class": class_name(user32.GetForegroundWindow()),
            }
            if shot:
                screenshot(shot)
        finally:
            stop.set()
            if actor:
                actor.join()
            if cleanup:
                cleanup()

    def thread_cpu(thread_id):
        if thread_id is None or thread_id not in start["threads"] or thread_id not in end["threads"]:
            return None
        return (end["threads"][thread_id] - start["threads"][thread_id]) * 1e3 / (end["time"] - start["time"])

    def switch_rate(thread_id):
        if thread_id is None or thread_id not in counters_start or thread_id not in counters_end:
            return None
        (count_start, seconds_start), (count_end, seconds_end) = counters_start[thread_id], counters_end[thread_id]
        return (count_end - count_start) / (seconds_end - seconds_start) if seconds_end > seconds_start else None

    def label(thread_id):
        if thread_id == zed.ui_thread:
            return "UI thread"
        return names.get(thread_id) or f"thread {thread_id}"

    busiest = sorted(((switch_rate(thread_id) or 0.0, label(thread_id)) for thread_id in counters_end), reverse=True)
    cpu, dwm = rate(start, end, "cpu"), rate(start, end, "dwm")
    return {
        "fps": rate(start, end, "frames"),
        "zed": cpu * 1e3,
        "switches": rate(start, end, "switches"),
        "ui_switches": switch_rate(zed.ui_thread),
        "vsync_switches": switch_rate(vsync_thread),
        "ui_cpu": thread_cpu(zed.ui_thread),
        "vsync_cpu": thread_cpu(vsync_thread),
        "dwm": dwm * 1e3 if dwm is not None else None,
        "busiest": [[name, round(switches, 1)] for switches, name in busiest[:8]],
        **state,
    }


def run_latency(variant, shot=None, presses=40, gap=0.3):
    """Times each key press until Zed logs the frame it caused, in milliseconds."""
    with Zed(variant, "latency", file=SHORT_FILE, cursor_blink=False) as zed:
        focused = foreground(zed.hwnd)
        time.sleep(SETTLE)
        samples = []
        with open(zed.stderr_path, "rb") as log:
            for _ in range(presses):
                time.sleep(gap)
                log.read()
                start = time.perf_counter()
                press(VK_J)
                while time.perf_counter() - start < 1:
                    if b"frame duration" in log.read():
                        samples.append((time.perf_counter() - start) * 1e3)
                        break
                    time.sleep(0.0005)
        if shot:
            screenshot(shot)
    if not samples:
        raise RuntimeError("no frame followed a key press")
    samples.sort()
    return {
        "latency": statistics.median(samples),
        "latency_p90": samples[int(len(samples) * 0.9)],
        "latency_max": samples[-1],
        "answered": len(samples),
        "foreground": focused,
    }


def probe(variant):
    """Checks that frames are still drawn after each kind of event that used to rely on a
    paint at every vblank. Returns {check: frames drawn}."""
    checks = {}
    with Zed(variant, "probe") as zed:
        def step(name, action, wait=3):
            before = zed.frames()
            action()
            time.sleep(wait)
            checks[name] = zed.frames() - before
            screenshot(os.path.join(RESULTS, f"probe-{name}-{variant}.png"))

        def type_five_letters():
            for _ in range(5):
                press(VK_J)
                time.sleep(0.2)

        checks["launch"] = zed.frames()
        screenshot(os.path.join(RESULTS, f"probe-launch-{variant}.png"))
        foreground(zed.hwnd)
        step("typing", type_five_letters)
        user32.ShowWindow(zed.hwnd, SW_MINIMIZE)
        time.sleep(3)
        step("restore", lambda: user32.ShowWindow(zed.hwnd, SW_RESTORE))

        def resize():
            user32.ShowWindow(zed.hwnd, SW_SHOWNORMAL)
            user32.SetWindowPos(zed.hwnd, None, 40, 40, 800, 560, SWP_NOZORDER | SWP_NOACTIVATE)

        step("resize", resize)
    return checks


def summarize(rows, runs):
    latency = [row for row in rows if row[0] == "latency"]
    rows = [row for row in rows if row[0] != "latency"]
    if rows:
        print()
        print(f"Medians of {runs} runs per build, rotating order; "
              f"{IDLE_WINDOW:.0f} s windows for the idle scenarios, {WINDOW:.0f} s for the others")
        table(rows, [("Frames/s", "fps"), ("Zed CPU ms/s", "zed"), ("Switches/s", "switches"),
                     ("UI thread switches/s", "ui_switches"), ("Vsync thread switches/s", "vsync_switches"),
                     ("dwm CPU ms/s", "dwm")])
    if latency:
        print()
        print(f"Key press to drawn frame in ms, per run of 40 presses; medians of {runs} runs per build")
        table(latency, [("Median", "latency"), ("90th percentile", "latency_p90"), ("Slowest", "latency_max")])
    print(f"Cells: {' → '.join(VARIANTS)}")
    for scenario, results in rows:
        print()
        print(f"{scenario}: foreground window and busiest threads by context switches/s in the last run")
        for variant in VARIANTS:
            if results[variant]:
                last = results[variant][-1]
                threads = ", ".join(f"{name} {switches}" for name, switches in last["busiest"])
                print(f"  {variant}: {last['foreground_class']}; {threads}")


def table(rows, columns):
    print("| Scenario | " + " | ".join(title for title, _ in columns) + " |")
    print("|---|" + "---|" * len(columns))
    for scenario, results in rows:
        def cell(key):
            medians = []
            for variant in VARIANTS:
                values = [result[key] for result in results[variant] if result.get(key) is not None]
                if not values:
                    return "n/a"
                medians.append(statistics.median(values))
            before, after = medians
            change = f" ({100 * (after - before) / before:+.0f}%)" if before else ""
            return f"{before:.1f} → {after:.1f}{change}"

        print(f"| {scenario} | " + " | ".join(cell(key) for _, key in columns) + " |")


def main():
    # The Windows console encoding can't print the arrows in the summary.
    sys.stdout.reconfigure(encoding="utf-8")
    runs = int(sys.argv[1])
    scenarios = sys.argv[2].split(",")
    os.makedirs(os.path.join(PROJECT, "src"), exist_ok=True)
    shutil.copy(os.path.join(ROOT, "crates", "gpui", "src", "window.rs"), LONG_FILE)
    with open(os.path.join(PROJECT, "README.md"), "w") as file:
        file.write("# Bench project\n")
    with open(SHORT_FILE, "w") as file:
        file.write("Bench notes\n")
    os.makedirs(RESULTS, exist_ok=True)
    raw = open(os.path.join(RESULTS, "runs.jsonl"), "w")
    compositor = dwm_pid()
    # A Zed that is already running would take over every launch as the first instance.
    for process in psutil.process_iter(["name"]):
        if (process.info["name"] or "").lower() == "zed.exe":
            kill_tree(process.pid)

    probes = {}
    for variant in VARIANTS:
        try:
            probes[variant] = probe(variant)
        except Exception as error:
            probes[variant] = {"error": repr(error)}
        print(json.dumps({"probe": variant, **probes[variant]}), flush=True)

    rows = []
    for scenario in scenarios:
        results = {variant: [] for variant in VARIANTS}
        for index in range(runs):
            shift = index % len(VARIANTS)
            for variant in VARIANTS[shift:] + VARIANTS[:shift]:
                shot = os.path.join(RESULTS, f"{scenario}-{variant}.png") if index == 0 else None

                def run():
                    if scenario == "latency":
                        return run_latency(variant, shot)
                    return run_once(scenario, variant, compositor, shot)

                try:
                    result = run()
                except Exception as error:
                    print(f"retrying {scenario} {variant}: {error!r}", flush=True)
                    try:
                        result = run()
                    except Exception as error:
                        print(f"skipping {scenario} {variant}: {error!r}", flush=True)
                        continue
                results[variant].append(result)
                record = dict(scenario=scenario, variant=variant, index=index, **result)
                raw.write(json.dumps(record) + "\n")
                raw.flush()
                print(json.dumps(record), flush=True)
        rows.append((scenario, results))

    steps = list(dict.fromkeys(step for checks in probes.values() for step in checks))
    print()
    print("Frames drawn in the 3 s after each probe step (launch: since start)")
    print("| Build | " + " | ".join(steps) + " |")
    print("|---|" + "---|" * len(steps))
    for variant, checks in probes.items():
        print(f"| {variant} | " + " | ".join(str(checks.get(step, "n/a")) for step in steps) + " |")
    summarize(rows, runs)


if __name__ == "__main__":
    main()
