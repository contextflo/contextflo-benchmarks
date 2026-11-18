"""Pick the held-out DABstep tasks: new questions the four notes were never written from.

Usage: uv run python -m dabstep.select_heldout [--per-family 3] [--min-support 20]

- Same eligibility as select_tasks.py: hard, not a giveaway answer ("Not Applicable" or an
  empty list), and an answer we can trust (a dev answer or --min-support accepted submissions).
- Excludes every task in tasks.txt (the 25 the notes were developed on).
- Balanced by question family (families.py): the same number from every family, so no
  template dominates. Order inside a family is a hash of the task id, so the pick is stable
  and anyone can reproduce it.
- Marks each family as seen (some of the 25 were from it, so the notes may have been shaped
  by it) or unseen (none of the 25 were).

Writes heldout.txt (task ids) and heldout.json (task, family, seen). Picked before any
held-out run; freeze.json records their hashes so a later change is visible.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from dabstep.families import FAMILIES, family
from dabstep.select_tasks import is_giveaway

HERE = Path(__file__).parent
ANSWER_KEY = HERE / "data" / "answer_key.jsonl"
DEV_TASKS = HERE / "tasks.txt"
HELDOUT_TXT = HERE / "heldout.txt"
HELDOUT_JSON = HERE / "heldout.json"


def stable_order(*, task_id: str) -> str:
    return hashlib.sha256(f"heldout:{task_id}".encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-family", type=int, default=3)
    parser.add_argument("--min-support", type=int, default=20)
    args = parser.parse_args()

    rows = [json.loads(line) for line in ANSWER_KEY.read_text().splitlines() if line.strip()]
    dev_ids = set(DEV_TASKS.read_text().split())
    dev_questions = {row["question"] for row in rows if row["task_id"] in dev_ids}
    dev_families = {family(question=row["question"]) for row in rows if row["task_id"] in dev_ids}

    eligible = [
        row for row in rows
        if row["level"] == "hard"
        and not is_giveaway(answer=row["answer"])
        and (row["source"] == "dev" or row["support"] >= args.min_support)
        and row["task_id"] not in dev_ids
        and row["question"] not in dev_questions
    ]
    by_family: dict[str, list[dict]] = defaultdict(list)
    for row in eligible:
        by_family[family(question=row["question"])].append(row)

    picked = []
    for name, _, _ in FAMILIES:
        group = sorted(by_family[name], key=lambda row: stable_order(task_id=row["task_id"]))
        if len(group) < args.per_family:
            raise SystemExit(f"family {name} has only {len(group)} eligible tasks, need {args.per_family}")
        picked.extend({"task_id": row["task_id"], "family": name, "seen": name in dev_families}
                      for row in group[: args.per_family])

    HELDOUT_TXT.write_text("".join(f"{row['task_id']}\n" for row in picked))
    HELDOUT_JSON.write_text(json.dumps(picked, indent=2) + "\n")
    unseen = sorted({row["family"] for row in picked if not row["seen"]})
    print(f"{len(picked)} tasks from {len(FAMILIES)} families ({args.per_family} each); "
          f"unseen families: {unseen} -> {HELDOUT_TXT.name}, {HELDOUT_JSON.name}")


if __name__ == "__main__":
    main()
