"""Build the answer key for DABstep tasks whose answers are not public.

DABstep publishes answers only for the dev split (tasks/dev.jsonl). For the rest, the
answer is hidden behind the leaderboard. But the leaderboard publishes every
submission's per-task result (task_scores/*.jsonl: the submitted answer plus whether
the official scorer accepted it against the hidden answer). An answer the scorer
accepted is a known-correct answer.

The scorer is tolerant (numeric tolerance, loose string and list matching), so a single
accepted answer may only be close to the hidden one. A task is used only when there is
consensus:

- at least MIN_SUPPORT submissions were scored correct on it, and
- every accepted answer matches the chosen answer under the official scorer.

The chosen answer is the most common accepted answer. The method is checked against
the dev split, where the real answers are known (see the "validation" block it prints).

Output: data/answer_key.jsonl, one row per usable task:
  task_id, level, question, guidelines, answer, source ("dev" or "leaderboard"), support, forms
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dabstep.scorer import question_scorer

DATA_DIR = Path(__file__).parent / "data"
RAW_DIR = DATA_DIR / "raw"
MIN_SUPPORT = 5


def read_jsonl(*, path: Path) -> list[dict]:
    with open(path) as source:
        return [json.loads(line) for line in source if line.strip()]


def accepted_answers_by_task() -> dict[str, list[str]]:
    accepted: dict[str, list[str]] = defaultdict(list)
    for path in sorted((RAW_DIR / "task_scores").glob("*.jsonl")):
        for row in read_jsonl(path=path):
            if row.get("score") is True and isinstance(row.get("agent_answer"), str):
                accepted[str(row["task_id"])].append(row["agent_answer"].strip())
    return accepted


def consensus(*, answers: list[str]) -> dict | None:
    """Most common accepted answer, if every accepted answer agrees with it under the scorer."""
    if len(answers) < MIN_SUPPORT:
        return None
    forms = Counter(answers)
    chosen = forms.most_common(1)[0][0]
    if not all(question_scorer(answer, chosen) for answer in forms):
        return None
    return {"answer": chosen, "support": len(answers), "forms": len(forms)}


def main() -> None:
    tasks = {str(task["task_id"]): task for task in read_jsonl(path=RAW_DIR / "tasks" / "all.jsonl")}
    dev = {str(task["task_id"]): task for task in read_jsonl(path=RAW_DIR / "tasks" / "dev.jsonl")}
    accepted = accepted_answers_by_task()

    # Validation: how often does the leaderboard consensus agree with a real dev answer?
    checked, agreed = 0, 0
    for task_id, task in dev.items():
        result = consensus(answers=accepted.get(task_id, []))
        if result is None:
            continue
        checked += 1
        if question_scorer(result["answer"], task["answer"]):
            agreed += 1
        else:
            print(f"  dev mismatch on task {task_id}: consensus {result['answer']!r} vs real {task['answer']!r}")
    print(f"validation: consensus matched the real answer on {agreed}/{checked} dev tasks with consensus")

    rows = []
    for task_id, task in tasks.items():
        base = {
            "task_id": task_id,
            "level": task["level"],
            "question": task["question"],
            "guidelines": task["guidelines"],
        }
        if task_id in dev:
            rows.append({**base, "answer": dev[task_id]["answer"], "source": "dev", "support": None, "forms": None})
            continue
        result = consensus(answers=accepted.get(task_id, []))
        if result is not None:
            rows.append({**base, **result, "source": "leaderboard"})

    with open(DATA_DIR / "answer_key.jsonl", "w") as out:
        for row in rows:
            out.write(json.dumps(row) + "\n")

    by_level = Counter(row["level"] for row in rows)
    print(f"answer key: {len(rows)} of {len(tasks)} tasks ({dict(by_level)})")


if __name__ == "__main__":
    sys.exit(main())
