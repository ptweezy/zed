#!/usr/bin/env python3
"""Sets GitBlame::blame_for_rows to main's version (before) or the pull request's (after).

Edits the text in place so that CRLF checkouts on Windows work too.
"""

import sys

PATH = "crates/editor/src/git/blame.rs"
BEFORE = """\
        rows.iter().map(move |info| {
            let buffer_id = info.buffer_id?;
            self.sync(cx, buffer_id);

"""
AFTER = """\
        let mut last_synced_buffer_id = None;
        rows.iter().map(move |info| {
            let buffer_id = info.buffer_id?;
            if last_synced_buffer_id != Some(buffer_id) {
                self.sync(cx, buffer_id);
                last_synced_buffer_id = Some(buffer_id);
            }

"""


def main():
    variant = sys.argv[1]
    if variant not in ("before", "after"):
        sys.exit("usage: set_variant.py before|after")
    with open(PATH, encoding="utf-8", newline="") as file:
        original = file.read()
    newline = "\r\n" if "\r\n" in original else "\n"
    before = BEFORE.replace("\n", newline)
    after = AFTER.replace("\n", newline)
    old, new = (after, before) if variant == "before" else (before, after)
    if original.count(new) == 1 and original.count(old) == 0:
        print(f"{PATH} is already set to {variant}")
        return
    # The before text is a substring of nothing in the after text, and the reverse.
    if original.count(old) != 1:
        sys.exit(f"expected one {'after' if variant == 'before' else 'before'} block in {PATH}")
    with open(PATH, "w", encoding="utf-8", newline="") as file:
        file.write(original.replace(old, new))
    print(f"{PATH} is set to {variant}")


main()
