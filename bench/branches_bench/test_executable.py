import json
import sys

executables = []
for line in sys.stdin:
    try:
        message = json.loads(line)
    except ValueError:
        continue
    if (
        message.get("reason") == "compiler-artifact"
        and message.get("executable")
        and message["target"]["name"] == "git"
        and message["profile"]["test"]
    ):
        executables.append(message["executable"])

if len(executables) != 1:
    sys.exit(f"expected one git test executable, found {executables}")
print(executables[0])
