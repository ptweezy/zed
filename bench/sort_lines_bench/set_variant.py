#!/usr/bin/env python3
"""Sets crates/editor/src/editor.rs to main's sort-lines keys (before) or the pull request's (after).

Edits the text in place so that CRLF checkouts on Windows work too.
"""

import sys

PATH = "crates/editor/src/editor.rs"
CHANGES = [
    (
        "lines.sort_by_key(|&line| line.chars().count())",
        "lines.sort_by_cached_key(|&line| line.chars().count())",
    ),
    (
        "lines.sort_by_key(|line| line.to_lowercase())",
        "lines.sort_by_cached_key(|line| line.to_lowercase())",
    ),
]


def main():
    variant = sys.argv[1]
    if variant not in ("before", "after"):
        sys.exit("usage: set_variant.py before|after")
    with open(PATH, encoding="utf-8", newline="") as file:
        original = file.read()
    source = original
    for before, after in CHANGES:
        old, new = (after, before) if variant == "before" else (before, after)
        if source.count(old) == 0 and source.count(new) == 1:
            continue
        if source.count(old) != 1:
            sys.exit(f"expected one {old!r} in {PATH}")
        source = source.replace(old, new)
    if source != original:
        with open(PATH, "w", encoding="utf-8", newline="") as file:
            file.write(source)
    print(f"{PATH} is set to {variant}")


main()
