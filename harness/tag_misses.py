"""Tag every wrong run with why it went wrong, for the write-up's "what went wrong" section.

Usage: uv run python -m harness.tag_misses --benchmark dabstep results/<folder> [results/<folder> ...] [--model claude-opus-5-5]

For each run scored wrong, a judge model reads the question, the expected answer, the agent's
answer, every query it ran (SQL plus a cut-down result) and the benchmark's documentation, and
picks one cause:

  ignored_documented_rule  the rule needed is in the documentation and the agent broke it
  undocumented_convention  the expected answer depends on a convention the documentation never states
  wrong_logic              a wrong filter, join or calculation not covered by either of the above
  no_answer                no final answer: an error, the turn cap, or the agent gave up
  answer_format            the right value in a form the scorer rejects

It writes misses.csv into each result folder (run_id, task_id, cause, rule, why) and prints the
counts. The judge is a model, so read the "why" column before quoting any number: it is a
first pass to check, not a verdict.

Needs ANTHROPIC_API_KEY. Costs a few cents per miss with Opus.
"""

import argparse
import csv
import importlib
import json
import os
from collections import Counter
from pathlib import Path

import anthropic
from dotenv import load_dotenv

CAUSES = ["ignored_documented_rule", "undocumented_convention", "wrong_logic", "no_answer", "answer_format"]
RESULT_PREVIEW_CHARS = 1500
QUERY_TOOL = "execute-query"

SCHEMA = {
    "type": "object",
    "properties": {
        "cause": {"type": "string", "enum": CAUSES},
        "rule": {"type": "string", "description": "The documented rule that was broken, quoted; empty if none"},
        "why": {"type": "string", "description": "One or two sentences on what the agent did and what was needed"},
    },
    "required": ["cause", "rule", "why"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You are reviewing why an AI agent got a data question wrong. You get the question, the
expected answer, the agent's answer, the queries it ran with their results, and the dataset's
documentation. Some agents ran with this documentation available and some without it; either way, judge
against the documentation: for an agent that never saw it, ignored_documented_rule means the
documentation would have covered the mistake.

Pick exactly one cause:
- ignored_documented_rule: the documentation states the rule the answer needs, and the agent's queries break it.
- undocumented_convention: getting the expected answer requires a convention the documentation never states
  (for example how to combine several matching rules). Use this even if the agent also made other mistakes,
  when following the documentation faithfully still would not reach the expected answer.
- wrong_logic: a wrong filter, join or calculation that neither of the above explains.
- no_answer: the agent produced no final answer.
- answer_format: the agent had the right value but in a form the scorer rejects.

Be specific in "why": name the column, filter or step. Quote the rule in "rule" when the cause is
ignored_documented_rule, otherwise leave it empty."""


def query_summary(*, tool_calls: list[dict]) -> str:
    parts = []
    for call in tool_calls:
        if call["name"] != QUERY_TOOL:
            continue
        sql = call["arguments"].get("query", "")
        parts.append(f"[{call['role']}{', ERROR' if call['is_error'] else ''}]\nSQL: {sql}\nRESULT: {call['result_text'][:RESULT_PREVIEW_CHARS]}")
    return "\n\n".join(parts) if parts else "(no queries run)"


def tag_run(*, client: anthropic.Anthropic, model: str, documentation: str, question: str, expected: str, trace: dict) -> dict:
    content = (
        f"<documentation>\n{documentation}\n</documentation>\n\n"
        f"<question>\n{question}\n</question>\n\n"
        f"<expected_answer>{expected}</expected_answer>\n"
        f"<agent_answer>{trace['answer']}</agent_answer>\n"
        f"<agent_error>{trace['error'] or ''}</agent_error>\n\n"
        f"<queries>\n{query_summary(tool_calls=trace['tool_calls'])}\n</queries>"
    )
    response = client.messages.create(
        model=model,
        max_tokens=4000,
        system=INSTRUCTIONS,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        return {"cause": "", "rule": "", "why": "judge declined"}
    return json.loads(next(block.text for block in response.content if block.type == "text"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("folders", nargs="+")
    parser.add_argument("--model", default="claude-opus-5-5")
    args = parser.parse_args()

    load_dotenv()
    benchmark = importlib.import_module(f"{args.benchmark}.benchmark")
    documentation = benchmark.documentation()
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace_id} if workspace_id else None)

    for folder in map(Path, args.folders):
        with open(folder / "traces.jsonl") as source:
            misses = [trace for trace in map(json.loads, source) if not trace["correct"]]
        questions = {task.task_id: task for task in benchmark.load_tasks(task_ids=sorted({trace["task_id"] for trace in misses}))}
        rows = []
        for trace in misses:
            task = questions[trace["task_id"]]
            tag = tag_run(client=client, model=args.model, documentation=documentation,
                          question=task.prompt, expected=task.expected, trace=trace)
            rows.append({"run_id": trace["run_id"], "task_id": trace["task_id"], **tag})
            print(f"{trace['run_id']}: {tag['cause']} - {tag['why']}", flush=True)
        with open(folder / "misses.csv", "w", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=["run_id", "task_id", "cause", "rule", "why"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"{folder.name}: {len(rows)} misses {dict(Counter(row['cause'] for row in rows))}")


if __name__ == "__main__":
    main()
