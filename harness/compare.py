"""Side-by-side summary of several result folders (e.g. the 2x2 of model x context state).

Usage: uv run python -m harness.compare results/<run-a> results/<run-b> ...
Prints one markdown table, a row per folder, and writes nothing.
"""

import json
import sys
from pathlib import Path

COLUMNS = [
    ("profile", "Context"),
    ("model", "Model"),
    ("runs", "Runs"),
    ("accuracy", "Accuracy"),
    ("total_cost_usd", "Total $"),
    ("cost_per_correct_usd", "$ per correct"),
    ("mean_sql_writing_tokens", "SQL-writing tokens"),
    ("mean_results_reading_tokens", "Result-reading tokens"),
    ("mean_bytes_exploration", "Exploration bytes"),
    ("mean_bytes_final", "Final-query bytes"),
    ("mean_tool_calls", "Tool calls"),
    ("mean_wall_seconds", "Seconds"),
]


def main() -> None:
    folders = [Path(arg) for arg in sys.argv[1:]]
    if not folders:
        raise SystemExit("pass one or more result folders")
    summaries = [json.loads((folder / "summary.json").read_text()) for folder in folders]
    print("| " + " | ".join(label for _, label in COLUMNS) + " |")
    print("|" + "---|" * len(COLUMNS))
    for summary in summaries:
        print("| " + " | ".join("" if summary.get(key) is None else str(summary[key]) for key, _ in COLUMNS) + " |")


if __name__ == "__main__":
    main()
