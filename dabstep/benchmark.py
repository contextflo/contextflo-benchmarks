"""DABstep adapter for the harness: which tasks, what the agent is told, how an answer scores.

The prompt is DABstep's own task text (question + answer guidelines). Two additions, the same
for every context state: how to hand back the answer, so the harness can read it without
guessing, and that the data is from 2023. The second one is there because an MCP server can
tell agents to assume the current year (Contextflo's does), which would send a question like
"in March" to the wrong year on a 2023 dataset; it is a fact about the data, not context.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from dabstep.scorer import question_scorer

HERE = Path(__file__).parent
ANSWER_KEY = HERE / "data" / "answer_key.jsonl"
TASKS_FILE = HERE / "tasks.txt"

NAME = "dabstep"

SYSTEM_PROMPT = (
    "You are a data analyst. You answer questions about the company's payments data using the tools "
    "you have, which give you access to the company's data warehouse. The payments data is from 2023. "
    "Work it out from the data; "
    "do not guess. When you are done, end your reply with one line in exactly this form:\n"
    "FINAL ANSWER: <answer>\n"
    "Follow the answer guidelines given with the question."
)


@dataclass(frozen=True)
class Task:
    task_id: str
    prompt: str
    expected: str


def load_tasks(*, task_ids: list[str] | None = None) -> list[Task]:
    with open(ANSWER_KEY) as source:
        key = {row["task_id"]: row for row in (json.loads(line) for line in source)}
    ids = task_ids if task_ids is not None else TASKS_FILE.read_text().split()
    missing = [task_id for task_id in ids if task_id not in key]
    if missing:
        raise SystemExit(f"no answer for task ids {missing}; rebuild with ground_truth.py")
    return [
        Task(
            task_id=task_id,
            prompt=f"{key[task_id]['question']}\n\nAnswer guidelines: {key[task_id]['guidelines']}",
            expected=key[task_id]["answer"],
        )
        for task_id in ids
    ]


def documentation() -> str:
    """DABstep's own documentation, the source the with-context state was built from. Used to
    judge whether a miss broke a documented rule or needed something the docs never say."""
    context_dir = HERE / "data" / "raw" / "context"
    return "\n\n".join((context_dir / name).read_text() for name in ("manual.md", "payments-readme.md"))


def score(*, answer: str | None, expected: str) -> bool:
    if answer is None:
        return False
    return question_scorer(answer, expected)
