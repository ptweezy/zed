import sys

source_path, harness_path = sys.argv[1], sys.argv[2]
with open(source_path, newline="") as source_file:
    source = source_file.read()
with open(harness_path) as harness_file:
    harness = harness_file.read()

newline = "\r\n" if "\r\n" in source else "\n"
anchor = f"    use super::*;{newline}    use gpui::TestAppContext;{newline}"
if source.count(anchor) != 1:
    sys.exit(f"expected one test module anchor in {source_path}, found {source.count(anchor)}")

with open(source_path, "w", newline="") as source_file:
    source_file.write(source.replace(anchor, anchor + harness.replace("\n", newline), 1))
