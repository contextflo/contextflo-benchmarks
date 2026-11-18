"""Rescore saved DABstep answers three ways, without rerunning the agent.

Usage:
  uv run python -m harness.rescore --out results/rescore \\
      --setup raw=results/<folder> --setup docs=results/<folder> ...

For every saved answer (runs.jsonl in each results folder), three verdicts:
  current   what the run recorded: DABstep's official scorer against our answer key.
  strict    the official scorer's numeric tolerance, but the sign must match and labels
            (card scheme, ACI, merchant) must match exactly. The official scorer drops a
            leading minus and compares "Label:number" answers on the number alone.
            Provisional: our answer key is leaderboard consensus, not the hidden answer.
  official  the leaderboard's own verdict on this exact answer string (whitespace
            trimmed), from DABstep's published per-submission scores: accepted, rejected,
            mixed, or unseen. This is the official scorer against the hidden answer, so it
            is sign-blind too. Many rejections of one string are one piece of evidence
            about that string, not many.

Writes:
  answers.csv    one row per saved answer, with a note wherever the verdicts disagree
  distinct.csv   one row per distinct (setup, task, answer), with run count
  summary.json   per setup: runs correct under each verdict, and distinct-answer evidence
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from dabstep.scorer import compare_numeric, question_scorer

TASK_SCORES_DIR = Path(__file__).parent.parent / "dabstep" / "data" / "raw" / "task_scores"
NUMBER = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)$")
THOUSANDS = re.compile(r"^[-+]?\d{1,3}(,\d{3})+(\.\d+)?$")
LABELLED = re.compile(r"^([^:]+):\s*(\S+)$")


def read_jsonl(*, path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_runs_csv(*, path: Path) -> list[dict]:
    with open(path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if row["correct"] not in ("True", "False"):
            raise SystemExit(f"{path}: unexpected correct value {row['correct']!r} in {row['run_id']}")
        row["correct"] = row["correct"] == "True"
        # csv writes None as an empty cell; no run answers with an empty string
        row["answer"] = row["answer"] if row["answer"] != "" else None
    return rows


def leaderboard_verdicts(*, task_ids: set[str]) -> dict[tuple[str, str], dict[str, int]]:
    """(task_id, trimmed answer) -> accepted / rejected submission counts."""
    verdicts: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"accepted": 0, "rejected": 0})
    for path in sorted(TASK_SCORES_DIR.glob("*.jsonl")):
        for row in read_jsonl(path=path):
            if row["task_id"] not in task_ids or row.get("agent_answer") is None:
                continue
            key = (row["task_id"], str(row["agent_answer"]).strip())
            verdicts[key]["accepted" if row["score"] else "rejected"] += 1
    return verdicts


def official_verdict(*, counts: dict[str, int] | None) -> str:
    if not counts:
        return "unseen"
    if counts["accepted"] and counts["rejected"]:
        return "mixed"
    return "accepted" if counts["accepted"] else "rejected"


def clean(*, text: str) -> str:
    """Lowercase, trim, and drop the list brackets and quotes agents wrap answers in."""
    value = text.strip().lower()
    value = re.sub(r"^\[|\]$", "", value).strip()
    return value.strip("'\" ")


def as_number(*, text: str) -> float | None:
    value = text.replace("$", "").replace("€", "").strip()
    if THOUSANDS.match(value):
        value = value.replace(",", "")
    return float(value) if NUMBER.match(value) else None


def strict_number(*, answer: float, expected: float) -> tuple[bool, str]:
    if (answer < 0) != (expected < 0) and not (answer == 0 or expected == 0):
        return False, "sign differs"
    return compare_numeric(abs(answer), abs(expected)), ""


def strict_item(*, answer: str, expected: str) -> tuple[bool, str]:
    expected_number = as_number(text=expected)
    if expected_number is not None:
        answer_number = as_number(text=answer)
        if answer_number is None:
            return False, "not a single number"
        return strict_number(answer=answer_number, expected=expected_number)
    labelled = LABELLED.match(expected)
    if labelled and as_number(text=labelled.group(2)) is not None:
        found = LABELLED.match(answer)
        if not found or as_number(text=found.group(2)) is None:
            return False, "not a label:number answer"
        if found.group(1).strip() != labelled.group(1).strip():
            return False, "label differs"
        return strict_number(answer=as_number(text=found.group(2)), expected=as_number(text=labelled.group(2)))
    return answer == expected, ""


def strict_score(*, answer: str | None, expected: str) -> tuple[bool, str]:
    if answer is None:
        return False, "no answer"
    answer_clean, expected_clean = clean(text=answer), clean(text=expected)
    if as_number(text=expected_clean) is None and re.search(r"[,;]", expected_clean):
        answer_items = sorted(clean(text=item) for item in re.split(r"[,;]", answer_clean) if item.strip())
        expected_items = sorted(clean(text=item) for item in re.split(r"[,;]", expected_clean) if item.strip())
        if len(answer_items) != len(expected_items):
            return False, "list length differs"
        for answer_item, expected_item in zip(answer_items, expected_items):
            matched, reason = strict_item(answer=answer_item, expected=expected_item)
            if not matched:
                return False, f"list item {answer_item!r}: {reason or 'differs'}"
        return True, ""
    return strict_item(answer=answer_clean, expected=expected_clean)


def note_for(*, current: bool, strict: bool, strict_reason: str, official: str) -> str:
    notes = []
    if current and not strict:
        notes.append(f"official scorer accepts, strict rejects ({strict_reason})")
    if strict and not current:
        notes.append("strict accepts, official scorer rejects (check normalization)")
    if official == "accepted" and not current:
        notes.append("leaderboard accepted this exact answer: our answer key may be wrong")
    if official == "rejected" and current:
        notes.append("leaderboard rejected this exact answer: our answer key or tolerance let it through")
    if official == "mixed":
        notes.append("leaderboard both accepted and rejected this exact answer (scorer changed over time?)")
    return "; ".join(notes)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="append", required=True, help="label=results folder, in climb order")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    setups = [tuple(item.split("=", 1)) for item in args.setup]
    # runs.csv, not runs.jsonl: every results folder has it (runs.jsonl came with --resume)
    rows_by_setup = {label: read_runs_csv(path=Path(folder) / "runs.csv") for label, folder in setups}
    task_ids = {row["task_id"] for rows in rows_by_setup.values() for row in rows}
    verdicts = leaderboard_verdicts(task_ids=task_ids)

    answers = []
    for label, rows in rows_by_setup.items():
        for row in rows:
            answer = row["answer"]
            # Recompute rather than trust the stored flag, so the official column is checked too
            current = answer is not None and question_scorer(str(answer), str(row["expected"]))
            if current != bool(row["correct"]):
                raise SystemExit(f"{label} {row['run_id']}: stored correct={row['correct']} but scorer says {current}")
            strict, strict_reason = strict_score(answer=answer, expected=str(row["expected"]))
            counts = verdicts.get((row["task_id"], str(answer).strip())) if answer is not None else None
            official = official_verdict(counts=counts)
            # Best evidence: the hidden answer's verdict where the leaderboard has one (majority
            # when mixed), else the strict score against our consensus key
            evidence = (counts["accepted"] > counts["rejected"]) if official != "unseen" else strict
            answers.append({
                "setup": label, "run_id": row["run_id"], "task_id": row["task_id"],
                "answer": answer, "expected": row["expected"],
                "current": current, "strict": strict, "official": official, "evidence": evidence,
                "official_accepted": counts["accepted"] if counts else 0,
                "official_rejected": counts["rejected"] if counts else 0,
                "note": note_for(current=current, strict=strict, strict_reason=strict_reason, official=official),
            })

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "answers.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(answers[0]))
        writer.writeheader()
        writer.writerows(answers)

    distinct: dict[tuple[str, str, str], dict] = {}
    for row in answers:
        key = (row["setup"], row["task_id"], str(row["answer"]))
        if key not in distinct:
            distinct[key] = {**{k: row[k] for k in ("setup", "task_id", "answer", "expected", "current", "strict",
                                                     "official", "evidence", "official_accepted", "official_rejected",
                                                     "note")},
                             "runs": 0}
        distinct[key]["runs"] += 1
    with open(out / "distinct.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(next(iter(distinct.values()))))
        writer.writeheader()
        writer.writerows(distinct.values())

    summary = {}
    for label, _ in setups:
        rows = [row for row in answers if row["setup"] == label]
        unique = [row for row in distinct.values() if row["setup"] == label]
        summary[label] = {
            "runs": len(rows),
            "current_correct": sum(row["current"] for row in rows),
            "strict_correct": sum(row["strict"] for row in rows),
            "evidence_correct": sum(row["evidence"] for row in rows),
            "questions_all_3_evidence": sum(
                1 for task in {row["task_id"] for row in rows}
                if all(row["evidence"] for row in rows if row["task_id"] == task)),
            "questions_all_3_current": sum(
                1 for task in {row["task_id"] for row in rows}
                if all(row["current"] for row in rows if row["task_id"] == task)),
            "questions_all_3_strict": sum(
                1 for task in {row["task_id"] for row in rows}
                if all(row["strict"] for row in rows if row["task_id"] == task)),
            "distinct_answers": len(unique),
            "distinct_by_official": {verdict: sum(1 for row in unique if row["official"] == verdict)
                                     for verdict in ("accepted", "rejected", "mixed", "unseen")},
            "runs_with_disagreement": sum(1 for row in rows if row["note"]),
        }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"written: {out}")


if __name__ == "__main__":
    main()
