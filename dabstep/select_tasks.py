"""Pick the hard DABstep tasks this benchmark runs, deterministically.

Usage: python select_tasks.py [--count 25] [--min-support 20]

- level == hard, from the answer key (dev answers, or leaderboard consensus with at
  least --min-support accepted submissions).
- Skips tasks whose answer is "Not Applicable" or an empty list: an agent can score
  on those by giving up, which says nothing about whether it can do the analysis.
- Spreads across question types (grouped by the question's first six words, which is
  how DABstep's templates differ), round-robin, so one template can't dominate.
- Order inside a type is a hash of the task id, so the pick is stable across machines
  and reruns without a random seed.

Writes tasks.txt (one task id per line). Commit it: the selection is part of the result.
"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
ANSWER_KEY = HERE / "data" / "answer_key.jsonl"
TASKS_FILE = HERE / "tasks.txt"


def is_giveaway(*, answer: str) -> bool:
    normalized = answer.strip().lower()
    return normalized in ("not applicable", "", "[]")


def question_type(*, question: str) -> str:
    return " ".join(question.split()[:6])


def stable_order(*, task_id: str) -> str:
    return hashlib.sha256(task_id.encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=25)
    parser.add_argument("--min-support", type=int, default=20)
    args = parser.parse_args()

    with open(ANSWER_KEY) as source:
        rows = [json.loads(line) for line in source]

    eligible = [
        row for row in rows
        if row["level"] == "hard"
        and not is_giveaway(answer=row["answer"])
        and (row["source"] == "dev" or row["support"] >= args.min_support)
    ]
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in eligible:
        by_type[question_type(question=row["question"])].append(row)
    for group in by_type.values():
        group.sort(key=lambda row: stable_order(task_id=row["task_id"]))

    picked: list[dict] = []
    types = sorted(by_type, key=lambda name: stable_order(task_id=name))
    while len(picked) < args.count and any(by_type[name] for name in types):
        for name in types:
            if by_type[name] and len(picked) < args.count:
                picked.append(by_type[name].pop(0))

    if len(picked) < args.count:
        raise SystemExit(f"only {len(picked)} eligible tasks, asked for {args.count}")

    TASKS_FILE.write_text("".join(f"{row['task_id']}\n" for row in picked))
    print(f"{len(picked)} tasks from {len({question_type(question=row['question']) for row in picked})} question types -> {TASKS_FILE.name}")


if __name__ == "__main__":
    sys.exit(main())
