#!/usr/bin/env python3
"""Builds the blame-rows harness with the release profile and copies it next to the others.

usage: build_variant.py <binaries-dir> <variant> [--allocs]

Writes <binaries-dir>/<variant> and, with --allocs, <binaries-dir>/<variant>-allocs.
"""

import json
import os
import shutil
import subprocess
import sys


def main():
    binaries_dir, variant = sys.argv[1], sys.argv[2]
    targets = {"bench_blame_rows": variant}
    if "--allocs" in sys.argv[3:]:
        targets["bench_blame_rows_allocs"] = variant + "-allocs"
    command = ["cargo", "test", "--release", "-p", "editor"]
    for target in targets:
        command += ["--test", target]
    command += ["--no-run", "--message-format=json-render-diagnostics"]
    process = subprocess.run(command, stdout=subprocess.PIPE, text=True, check=True)
    executables = {}
    for line in process.stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            message.get("reason") == "compiler-artifact"
            and message["target"]["name"] in targets
            and message.get("executable")
        ):
            executables.setdefault(message["target"]["name"], []).append(message["executable"])
    suffix = ".exe" if os.name == "nt" else ""
    os.makedirs(binaries_dir, exist_ok=True)
    for target, name in targets.items():
        found = executables.get(target, [])
        if len(found) != 1:
            sys.exit(f"expected one {target} executable, found {found}")
        destination = os.path.join(binaries_dir, name + suffix)
        shutil.copy2(found[0], destination)
        print(f"{found[0]} -> {destination}")


main()
