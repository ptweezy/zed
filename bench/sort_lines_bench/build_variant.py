#!/usr/bin/env python3
"""Builds the sort-lines harness with the release profile and copies it to the given path."""

import json
import os
import shutil
import subprocess
import sys


def main():
    destination = sys.argv[1]
    command = [
        "cargo",
        "test",
        "--release",
        "-p",
        "editor",
        "--test",
        "bench_sort_lines",
        "--no-run",
        "--message-format=json-render-diagnostics",
    ]
    process = subprocess.run(command, stdout=subprocess.PIPE, text=True, check=True)
    executables = []
    for line in process.stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            message.get("reason") == "compiler-artifact"
            and message["target"]["name"] == "bench_sort_lines"
            and message.get("executable")
        ):
            executables.append(message["executable"])
    if len(executables) != 1:
        sys.exit(f"expected one harness executable, found {executables}")
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    shutil.copy2(executables[0], destination)
    print(f"{executables[0]} -> {destination}")


main()
