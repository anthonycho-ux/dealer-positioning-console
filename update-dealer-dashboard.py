#!/usr/bin/env python3
"""Update dealer dashboard data. Called by cron. Writes JSON + HTML report."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / 'reports'
PIPELINE = ROOT / 'cboe-gex-pipeline.py'

def main():
    REPORTS.mkdir(parents=True, exist_ok=True)

    # 1. Generate JSON data for dashboard
    result = subprocess.run(
        [sys.executable, str(PIPELINE), '--json'],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        print(f"❌ Pipeline JSON failed: {result.stderr[:200]}", file=sys.stderr)
        return 1

    data = json.loads(result.stdout)
    json_path = REPORTS / 'dealer-data.json'
    with open(json_path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"✅ dealer-data.json ({len(result.stdout)} bytes)")

    # 2. Run the optional local comparison engine when installed beside this script.
    cem_compare = ROOT / 'cem-gex-compare.py'
    if cem_compare.exists():
        result_cem = subprocess.run(
            [sys.executable, str(cem_compare)],
            capture_output=True, text=True, timeout=120
        )
        if result_cem.returncode == 0:
            print("✅ Cem comparison done")
        else:
            print(f"⚠️ Cem compare: {result_cem.stderr[:100]}")

    # 3. Generate HTML report
    html_path = REPORTS / 'dealer-brief.html'
    result2 = subprocess.run(
        [sys.executable, str(PIPELINE), '--save', str(html_path)],
        capture_output=True, text=True, timeout=300
    )
    if result2.returncode != 0:
        print(f"⚠️ HTML save: {result2.stderr[:100]}")
    else:
        print(f"✅ dealer-brief.html")

    return 0

if __name__ == '__main__':
    sys.exit(main())
