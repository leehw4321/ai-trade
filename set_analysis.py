#!/usr/bin/env python3
"""Usage: python set_analysis.py draft.json  -> stamps the time and writes analysis.json for the /market page."""
import json
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for key in ("summary", "issues"):
    if key not in data:
        raise SystemExit(f"missing '{key}'")
data["time"] = time.time()
tmp = root / "analysis.tmp"
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
tmp.replace(root / "analysis.json")
print("analysis.json updated")
