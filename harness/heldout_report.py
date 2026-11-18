"""Score the held-out batches the way dabstep/freeze.json fixes, and export receipts.

Usage:
  uv run python -m harness.heldout_report --out results/heldout-report \\
      --setup heldout-tables=results/<folder> --setup heldout-docs=results/<folder> \\
      --setup heldout-docs-notes=results/<folder>

Per answer, three verdicts (see freeze.json "scoring"):
  evidence   the leaderboard's majority verdict on this exact answer string if it was ever
             submitted, else strict against the most precise accepted answer (marked unverified)
  strict     strict match against the most precise answer the leaderboard accepted
  current    the official scorer against our consensus key, as recorded at run time

Writes:
  answers.csv               one row per run with all verdicts and a note where they disagree
  summary.json              per setup: accuracy, consistency, by family, seen vs unseen families, cost, bytes
  questions/<task_id>.json  one file per question for the receipts UI: the question, its family,
                            the answer, and every setup's runs (answer, verdicts, cost, the queries it
                            ran and what came back, the final reply)
  questions/index.json      the list of questions with per-setup scores, for the UI's overview
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from dabstep.families import describe
from harness.rescore import (TASK_SCORES_DIR, as_number, official_verdict, read_jsonl, read_runs_csv,
                             strict_score)

HERE = Path(__file__).parent.parent
ANSWER_KEY = HERE / "dabstep" / "data" / "answer_key.jsonl"
HELDOUT_JSON = HERE / "dabstep" / "heldout.json"
# Enough of a tool result to see what came back; the full text stays in traces.jsonl
RESULT_PREVIEW_CHARS = 4000


def decimals(*, text: str) -> int:
    match = re.search(r"\.(\d+)", text)
    return len(match.group(1)) if match else 0


def leaderboard(*, task_ids: set[str]) -> tuple[dict, dict]:
    """(task, answer) -> accepted/rejected counts, and task -> accepted answer -> count."""
    verdicts: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"accepted": 0, "rejected": 0})
    accepted: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for path in sorted(TASK_SCORES_DIR.glob("*.jsonl")):
        for row in read_jsonl(path=path):
            if row["task_id"] not in task_ids or row.get("agent_answer") is None:
                continue
            answer = str(row["agent_answer"]).strip()
            verdicts[(row["task_id"], answer)]["accepted" if row["score"] else "rejected"] += 1
            if row["score"]:
                accepted[row["task_id"]][answer] += 1
    return verdicts, accepted


def precise_key(*, accepted: dict[str, int], consensus: str) -> str:
    """The most precise answer the leaderboard accepted at least twice (a single acceptance can
    be a fluke of the tolerance); ties go to the most often accepted. Falls back to our key."""
    # Only single-number answers are refined: the official scorer accepts a bare number for a
    # labelled list (it ignores labels), so for any other shape the consensus key stands
    if as_number(text=consensus.strip()) is None:
        return consensus
    numeric = [answer for answer, count in accepted.items() if count >= 2 and as_number(text=answer) is not None]
    if not numeric:
        return consensus
    return max(numeric, key=lambda answer: (decimals(text=answer), accepted[answer]))


def run_receipt(*, trace: dict) -> dict:
    final_reply = ""
    for message in reversed(trace.get("transcript") or []):
        if message.get("role") == "assistant":
            content = message.get("content")
            texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"] \
                if isinstance(content, list) else [str(content)]
            final_reply = "\n".join(text for text in texts if text)
            if final_reply:
                break
    return {
        "steps": [
            {"tool": call["name"], "role": call["role"], "arguments": call["arguments"], "is_error": call["is_error"],
             "result_preview": (call.get("result_text") or "")[:RESULT_PREVIEW_CHARS],
             "result_truncated": len(call.get("result_text") or "") > RESULT_PREVIEW_CHARS,
             "started_at": call["started_at"], "ended_at": call["ended_at"]}
            for call in trace["tool_calls"]
        ],
        "final_reply": final_reply,
        "stop_reason": trace.get("stop_reason"),
        "error": trace.get("error"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="append", required=True, help="label=results folder")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    setups = [tuple(item.split("=", 1)) for item in args.setup]
    heldout = {row["task_id"]: row for row in json.loads(HELDOUT_JSON.read_text())}
    key = {row["task_id"]: row for row in (json.loads(line) for line in ANSWER_KEY.read_text().splitlines() if line.strip())}
    verdicts, accepted = leaderboard(task_ids=set(heldout))
    precise = {task: precise_key(accepted=accepted[task], consensus=key[task]["answer"]) for task in heldout}

    answers = []
    runs_by_task: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for label, folder in setups:
        rows = read_runs_csv(path=Path(folder) / "runs.csv")
        traces = {record["run_id"]: record for record in read_jsonl(path=Path(folder) / "traces.jsonl")}
        task_ids = {row["task_id"] for row in rows}
        if task_ids != set(heldout):
            raise SystemExit(f"{label}: tasks differ from heldout.json ({len(task_ids)} vs {len(heldout)})")
        for row in rows:
            task, answer = row["task_id"], row["answer"]
            counts = verdicts.get((task, str(answer).strip())) if answer is not None else None
            official = official_verdict(counts=counts)
            strict, strict_reason = strict_score(answer=answer, expected=precise[task])
            evidence = (counts["accepted"] > counts["rejected"]) if official != "unseen" else strict
            notes = []
            if official == "unseen":
                notes.append("never submitted to the leaderboard: evidence is the strict check, unverified")
            if official == "mixed":
                notes.append("leaderboard both accepted and rejected this exact answer; counted by majority")
            if row["correct"] != evidence:
                notes.append(f"consensus key says {'right' if row['correct'] else 'wrong'}, evidence says "
                             f"{'right' if evidence else 'wrong'}")
            if strict_reason and not strict:
                notes.append(f"strict: {strict_reason}")
            record = {
                "setup": label, "run_id": row["run_id"], "task_id": task, "family": heldout[task]["family"],
                "family_seen": heldout[task]["seen"], "answer": answer, "consensus_key": key[task]["answer"],
                "precise_key": precise[task], "evidence": evidence, "official": official,
                "official_accepted": counts["accepted"] if counts else 0,
                "official_rejected": counts["rejected"] if counts else 0,
                "strict": strict, "current": row["correct"],
                "cost_usd": float(row["cost_usd"]), "wall_seconds": float(row["wall_seconds"]),
                "tool_calls": int(row["tool_calls"]),
                "billed_bytes": sum(int(row[column]) for column in ("billed_final", "billed_exploration", "billed_context")
                                    if row.get(column) not in (None, "")),
                "context_hash": row["context_hash"], "note": "; ".join(notes),
            }
            answers.append(record)
            runs_by_task[task][label].append({**record, **run_receipt(trace=traces[row["run_id"]])})

    out = Path(args.out)
    (out / "questions").mkdir(parents=True, exist_ok=True)
    with open(out / "answers.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(answers[0]))
        writer.writeheader()
        writer.writerows(answers)

    def score_block(*, rows: list[dict]) -> dict:
        tasks = sorted({row["task_id"] for row in rows})
        per_task = {task: sum(row["evidence"] for row in rows if row["task_id"] == task) for task in tasks}
        runs_per = max(len([row for row in rows if row["task_id"] == task]) for task in tasks)
        return {
            "tasks": len(tasks), "runs": len(rows),
            "evidence_correct": sum(row["evidence"] for row in rows),
            "evidence_accuracy": round(sum(row["evidence"] for row in rows) / len(rows), 4),
            "strict_accuracy": round(sum(row["strict"] for row in rows) / len(rows), 4),
            "current_accuracy": round(sum(row["current"] for row in rows) / len(rows), 4),
            "tasks_right_every_run": sum(1 for count in per_task.values() if count == runs_per),
            "tasks_never_right": sum(1 for count in per_task.values() if count == 0),
            "unverified_runs": sum(1 for row in rows if row["official"] == "unseen"),
        }

    summary = {}
    for label, folder in setups:
        rows = [row for row in answers if row["setup"] == label]
        by_family = defaultdict(list)
        for row in rows:
            by_family[row["family"]].append(row)
        summary[label] = {
            "folder": folder,
            "context_hashes": sorted({row["context_hash"] for row in rows}),
            **score_block(rows=rows),
            "seen_families": score_block(rows=[row for row in rows if row["family_seen"]]),
            "unseen_families": score_block(rows=[row for row in rows if not row["family_seen"]]),
            "by_family": {name: score_block(rows=family_rows) for name, family_rows in sorted(by_family.items())},
            "model_cost_usd": round(sum(row["cost_usd"] for row in rows), 4),
            "model_cost_per_run_usd": round(sum(row["cost_usd"] for row in rows) / len(rows), 5),
            "mean_wall_seconds": round(sum(row["wall_seconds"] for row in rows) / len(rows), 1),
            "mean_billed_bytes": round(sum(row["billed_bytes"] for row in rows) / len(rows)),
        }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))

    index = []
    for task in heldout:
        question = {
            "task_id": task, "question": key[task]["question"], "guidelines": key[task]["guidelines"],
            "family": heldout[task]["family"], "family_description": describe(name=heldout[task]["family"]),
            "family_seen": heldout[task]["seen"], "consensus_key": key[task]["answer"], "precise_key": precise[task],
            "setups": {label: runs_by_task[task][label] for label, _ in setups},
        }
        (out / "questions" / f"{task}.json").write_text(json.dumps(question, indent=2, default=str))
        index.append({
            "task_id": task, "question": key[task]["question"], "family": heldout[task]["family"],
            "family_seen": heldout[task]["seen"],
            "scores": {label: sum(run["evidence"] for run in runs_by_task[task][label]) for label, _ in setups},
            "runs_per_setup": {label: len(runs_by_task[task][label]) for label, _ in setups},
        })
    (out / "questions" / "index.json").write_text(json.dumps(index, indent=2))
    print(json.dumps({label: {k: v for k, v in block.items() if k not in ("by_family",)} for label, block in summary.items()},
                     indent=2))
    print(f"written: {out}")


if __name__ == "__main__":
    main()
