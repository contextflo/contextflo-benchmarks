"""Trim the held-out receipts (harness.heldout_report questions/) into the public files the
blog's receipts explorer reads.

Usage:
  uv run python -m harness.export_receipts --report results/heldout-report --out <dir>

Writes <dir>/index.json (one row per question, for the list and filters) and <dir>/<task_id>.json
(one per question, loaded when opened). Each step becomes a plain line of what it found; queries
also keep the SQL, the first QUERY_ROWS rows and the business definitions they used. The exact tool
call and response ride along for every step, shown behind a closed toggle.
"""

import argparse
import json
from pathlib import Path

REPO = Path(__file__).parent.parent
QUERY_ROWS = 8
REPLY_CHARS = 3000
SETUPS = (("heldout-tables", "Only the tables"), ("heldout-docs", "+ docs"), ("heldout-docs-notes", "+ docs and notes"))


def outcome(*, scores: dict[str, int]) -> str:
    tables, docs, notes = (scores[key] for key, _ in SETUPS)
    if tables == docs == notes == 3:
        return "Right everywhere"
    if tables == docs == notes == 0:
        return "Wrong everywhere"
    if notes > max(tables, docs):
        return "Notes helped"
    if notes < max(tables, docs):
        return "Notes hurt"
    return "No change"


def parsed(*, text: str | None) -> dict | None:
    try:
        value = json.loads(text or "")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def count_line(*, names: list[str], noun: str) -> str:
    if not names:
        return f"No {noun}s found"
    return f"{len(names)} {noun}{'s' if len(names) != 1 else ''} found"


def found_items(*, call: dict) -> list[str]:
    """The names a lookup step returned, shown in full under its line."""
    result = parsed(text=call.get("result_text")) if not call["is_error"] else None
    if result is None:
        return []
    if call["name"] == "find-related-resources":
        return [item["name"] for key in ("businessConcepts", "metrics", "savedQueries") for item in result.get(key, [])]
    if call["name"] == "list-tables":
        return [short_table(name=item["fullyQualifiedName"]) for item in result.get("tables", [])]
    return []


def cell(*, value):
    """Query cells as plain values: lists and records (BigQuery arrays, structs) become JSON text."""
    return json.dumps(value) if isinstance(value, (list, dict)) else value


def short_table(*, name: str) -> str:
    return name.rsplit(".", 1)[-1]


def detail(*, call: dict, concepts: dict[str, str]) -> str:
    """One plain line saying what the step found, built from the full tool result."""
    name, result = call["name"], parsed(text=call.get("result_text"))
    if call["is_error"] or result is None:
        return (call.get("result_text") or "No result")[:300]
    if name == "getOrgInfo":
        resources = result.get("resources", {})
        count = resources.get("business_concepts", 0)
        return f"{count} business definition{'s' if count != 1 else ''} set up" if count else "No business definitions set up"
    if name == "find-related-resources":
        return count_line(names=found_items(call=call), noun="saved definition")
    if name == "list-tables":
        return count_line(names=found_items(call=call), noun="table")
    if name == "get-table-context":
        return "Read " + ", ".join(short_table(name=item["fullyQualifiedName"]) for item in result.get("data", []))
    if name in ("get-business-concept", "get-metric", "get-saved-query"):
        return result.get("name") or next((value.get("name") for value in result.values() if isinstance(value, dict)), "") or ""
    return ""


def step(*, call: dict, concepts: dict[str, str]) -> dict:
    arguments = call["arguments"] or {}
    item = {
        "tool": call["name"],
        "final": call["role"] == "final",
        "is_error": call["is_error"],
        "detail": detail(call=call, concepts=concepts),
        "items": found_items(call=call),
        "why": None, "sql": None, "columns": [], "rows": [], "row_count": 0, "used": [],
        # Nothing held back: the exact call and response, behind a closed toggle in the UI
        "raw_input": arguments,
        "raw_output": call.get("result_text") or "",
    }
    if call["name"] != "execute-query" or call["is_error"]:
        return item
    result = parsed(text=call.get("result_text")) or {}
    data = result.get("data") or []
    references = arguments.get("resourceReferences") or []
    item.update({
        "why": arguments.get("llmReasoning"),
        "sql": arguments.get("query"),
        "columns": list(data[0]) if data else [],
        "rows": [[cell(value=row.get(column)) for column in data[0]] for row in data[:QUERY_ROWS]] if data else [],
        "row_count": len(data),
        "detail": f"{len(data)} row{'s' if len(data) != 1 else ''} back",
        # Business definitions (the docs' concepts and the notes) the agent said this query relied on
        "used": [concepts[ref.split(":", 1)[1]] for ref in references
                 if ref.startswith("business-concept:") and ref.split(":", 1)[1] in concepts],
    })
    return item


def concept_names(*, calls: list[dict]) -> dict[str, str]:
    names = {}
    for call in calls:
        result = parsed(text=call.get("result_text")) if not call["is_error"] else None
        if result is None:
            continue
        for concept in result.get("businessConcepts", []):
            names[concept["id"]] = concept["name"]
        if call["name"] == "get-business-concept" and "id" in result and "name" in result:
            names[result["id"]] = result["name"]
    return names


def run(*, raw: dict, trace: dict) -> dict:
    if raw["official"] in ("accepted", "rejected", "mixed"):
        verdict_source = f"scoreboard: accepted {raw['official_accepted']}x, rejected {raw['official_rejected']}x"
    else:
        verdict_source = "never submitted to the scoreboard: strict check against our answer sheet"
    return {
        "try": int(raw["run_id"].rsplit("-", 1)[1]),
        "answer": raw["answer"],
        "right": raw["evidence"],
        "verdict_source": verdict_source,
        "cost_usd": raw["cost_usd"],
        "seconds": raw["wall_seconds"],
        "billed_mb": round(raw["billed_bytes"] / 1_000_000, 1),
        "steps": [step(call=call, concepts=concept_names(calls=trace["tool_calls"])) for call in trace["tool_calls"]],
        "final_reply": (raw["final_reply"] or "")[:REPLY_CHARS],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    source = Path(args.report) / "questions"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # Full tool results, for the plain-language step lines (the report keeps only previews)
    summary = json.loads((Path(args.report) / "summary.json").read_text())
    traces = {}
    for key, _ in SETUPS:
        for line in (REPO / summary[key]["folder"] / "traces.jsonl").read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                traces[record["run_id"]] = record
    # Hand-written, one line per question still missed with docs + notes
    misses = json.loads((REPO / "dabstep" / "heldout_misses.json").read_text())
    index = []
    for row in json.loads((source / "index.json").read_text()):
        question = json.loads((source / f"{row['task_id']}.json").read_text())
        scores = {key: row["scores"][key] for key, _ in SETUPS}
        (out / f"{row['task_id']}.json").write_text(json.dumps({
            "task_id": row["task_id"],
            "question": question["question"],
            "guidelines": question["guidelines"],
            "type": question["family_description"],
            "expected": question["precise_key"],
            "what_went_wrong": misses.get(row["task_id"]),
            "setups": [
                {"key": key, "label": label,
                 "runs": sorted((run(raw=raw, trace=traces[raw["run_id"]]) for raw in question["setups"][key]),
                                key=lambda item: item["try"])}
                for key, label in SETUPS
            ],
        }))
        index.append({
            "task_id": row["task_id"],
            "question": question["question"],
            "type": question["family_description"],
            "outcome": outcome(scores=scores),
            "scores": [{"label": label, "right": scores[key], "tries": row["runs_per_setup"][key]} for key, label in SETUPS],
        })
    (out / "index.json").write_text(json.dumps(index))
    print(f"wrote {len(index)} questions to {out}")


if __name__ == "__main__":
    main()
