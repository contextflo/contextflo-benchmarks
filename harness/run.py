"""Run a benchmark through an MCP server and write per-run counters plus a summary.

Usage:
  uv run python -m harness.run --benchmark dabstep --profile <mcp-profile> --model <model-id> \\
      [--runs 3] [--limit N] [--tasks 1712,1810] \\
      --bq-project <gcp-project> --bq-principal <service-account-email> [--bq-location US]

- --profile picks the MCP identity, and so the org and its context state.
- Each run is saved the moment it ends. --max-runs 18 makes ~15 min batches; continue one
  with --resume <results folder> (same other flags). A lost MCP session or unreachable API
  stops the batch without saving that run, so the resume redoes it.
- Runs execute one at a time (bytes are matched to tool calls by time window).
- Writes results/<timestamp>__<benchmark>__<profile>__<model>/
    runs.csv        one row per run: correct, tokens by kind, bytes by kind, tool calls, time, $
    traces.jsonl    full transcript and tool calls of every run
    summary.json    accuracy, total $, $ per correct answer, means
Needs ANTHROPIC_API_KEY, and Application Default Credentials that can read the
project's BigQuery job history (only for the bytes columns).

Token columns. A tool result's tokens are counted once, when it enters the context:
  context_result_tokens      schema and context lookups (list-tables, get-table-context, ...)
  exploration_result_tokens  results of every execute-query except the final one (samples, retries)
  final_result_tokens        result of the final execute-query, the rows the answer is read from
  sql_writing_tokens         output_tokens + context_result_tokens + exploration_result_tokens
  results_reading_tokens     final_result_tokens
The billed token columns (input, output, cache write, cache read) are what the API
charged, history re-reads included; cost_usd is computed from those.
"""

import argparse
import asyncio
import csv
import importlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from harness import agent, bigquery_jobs, pricing, snapshot
from harness.login import DEFAULT_SERVER
from harness.mcp_connection import connect

RESULTS_DIR = Path(__file__).parent.parent / "results"

CSV_COLUMNS = [
    "run_id", "benchmark", "profile", "model", "task_id", "run_index", "correct", "answer", "expected",
    "error", "stop_reason", "turns", "tool_calls", "execute_query_calls", "failed_tool_calls", "wall_seconds",
    "input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens",
    "context_result_tokens", "exploration_result_tokens", "final_result_tokens",
    "sql_writing_tokens", "results_reading_tokens",
    "bytes_final", "bytes_exploration", "bytes_context", "bytes_unattributed",
    "billed_final", "billed_exploration", "billed_context", "billed_unattributed",
    "cost_usd", "setup", "context_hash",
]
BYTE_COLUMNS = [column for column in CSV_COLUMNS if column.startswith(("bytes_", "billed_"))]


def tokens_by_role(*, calls: list[agent.ToolCall]) -> dict[str, int]:
    totals = {"context": 0, "exploration": 0, "final": 0}
    for call in calls:
        totals[call.role] += call.result_tokens
    return totals


def run_row(*, run_id: str, benchmark: str, profile: str, task, run_index: int, trace: agent.RunTrace,
            correct: bool, setup: str, context_hash: str) -> dict:
    by_role = tokens_by_role(calls=trace.tool_calls)
    return {
        "run_id": run_id,
        "benchmark": benchmark,
        "profile": profile,
        "model": trace.model,
        "task_id": task.task_id,
        "run_index": run_index,
        "correct": correct,
        "answer": trace.answer,
        "expected": task.expected,
        "error": trace.error,
        "stop_reason": trace.stop_reason,
        "turns": trace.turns,
        "tool_calls": len(trace.tool_calls),
        "execute_query_calls": sum(1 for call in trace.tool_calls if call.name == agent.QUERY_TOOL),
        "failed_tool_calls": sum(1 for call in trace.tool_calls if call.is_error),
        "wall_seconds": round(agent.wall_seconds(trace=trace), 1),
        "input_tokens": trace.input_tokens,
        "output_tokens": trace.output_tokens,
        "cache_write_tokens": trace.cache_write_tokens,
        "cache_read_tokens": trace.cache_read_tokens,
        "context_result_tokens": by_role["context"],
        "exploration_result_tokens": by_role["exploration"],
        "final_result_tokens": by_role["final"],
        "sql_writing_tokens": trace.output_tokens + by_role["context"] + by_role["exploration"],
        "results_reading_tokens": by_role["final"],
        **{column: None for column in BYTE_COLUMNS},
        "cost_usd": round(trace.cost_usd, 6),
        "setup": setup,
        "context_hash": context_hash,
    }


def summarize(*, rows: list[dict]) -> dict:
    runs = len(rows)
    correct = sum(1 for row in rows if row["correct"])
    cost = sum(row["cost_usd"] for row in rows)

    def mean(column: str) -> float | None:
        values = [row[column] for row in rows if row[column] is not None]
        return round(sum(values) / len(values), 1) if values else None

    return {
        "runs": runs,
        "correct": correct,
        "accuracy": round(correct / runs, 4) if runs else None,
        "total_cost_usd": round(cost, 4),
        "cost_per_correct_usd": round(cost / correct, 4) if correct else None,
        "mean_wall_seconds": mean("wall_seconds"),
        "mean_tool_calls": mean("tool_calls"),
        "mean_sql_writing_tokens": mean("sql_writing_tokens"),
        "mean_results_reading_tokens": mean("results_reading_tokens"),
        "mean_bytes_final": mean("bytes_final"),
        "mean_bytes_exploration": mean("bytes_exploration"),
        "mean_billed_final": mean("billed_final"),
        "mean_billed_exploration": mean("billed_exploration"),
        "runs_with_errors": sum(1 for row in rows if row["error"]),
    }


def trace_record(*, row: dict, trace: agent.RunTrace) -> dict:
    record = asdict(trace)
    return {"run_id": row["run_id"], "task_id": row["task_id"], "run_index": row["run_index"], "correct": row["correct"], **record}


def read_jsonl(*, path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_jsonl(*, path: Path, record: dict) -> None:
    with open(path, "a") as out:
        out.write(json.dumps(record, default=str) + "\n")


def fill_bytes(*, rows: list[dict], traces: dict[str, dict], args: argparse.Namespace) -> None:
    """Bytes for every saved run that has none yet, from the saved trace's call windows."""
    for row in rows:
        if row.get("billed_final") is not None:
            continue
        trace = traces[row["run_id"]]
        calls = [
            SimpleNamespace(started_at=datetime.fromisoformat(call["started_at"]),
                            ended_at=datetime.fromisoformat(call["ended_at"]), role=call["role"])
            for call in trace["tool_calls"]
        ]
        jobs = bigquery_jobs.fetch_jobs(project=args.bq_project, location=args.bq_location, principal=args.bq_principal,
                                        since=datetime.fromisoformat(trace["started_at"]),
                                        until=datetime.fromisoformat(trace["ended_at"]))
        row.update(bigquery_jobs.attribute(jobs=jobs, calls=calls))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--limit", type=int, help="only the first N tasks (sample runs)")
    parser.add_argument("--tasks", help="comma-separated task ids, instead of the benchmark's task list")
    parser.add_argument("--task-file", help="file of task ids (one per line), instead of the benchmark's task list")
    parser.add_argument("--setup", required=True,
                        help="name of the condition this batch measures (e.g. heldout-docs); names the results folder")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--bq-project", required=True)
    parser.add_argument("--bq-principal", required=True, help="service account the MCP server queries BigQuery as")
    parser.add_argument("--bq-location", default="US")
    parser.add_argument("--resume", help="results folder of an unfinished run; skips the runs it already saved")
    parser.add_argument("--max-runs", type=int, help="stop after this many new runs (batches of ~15 min at 18)")
    args = parser.parse_args()

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set ANTHROPIC_API_KEY")
    prices = pricing.load_prices(model=args.model)
    benchmark = importlib.import_module(f"{args.benchmark}.benchmark")
    if args.tasks and args.task_file:
        raise SystemExit("pass --tasks or --task-file, not both")
    task_ids = args.tasks.split(",") if args.tasks else Path(args.task_file).read_text().split() if args.task_file else None
    tasks = benchmark.load_tasks(task_ids=task_ids)
    if args.limit:
        tasks = tasks[: args.limit]

    if args.resume:
        out_dir = Path(args.resume)
        if not out_dir.is_dir():
            raise SystemExit(f"--resume: no results folder at {out_dir}")
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = RESULTS_DIR / f"{stamp}__{args.benchmark}__{args.setup}__{args.model}"
        out_dir.mkdir(parents=True)
    config_path = out_dir / "config.json"
    config = {"benchmark": args.benchmark, "setup": args.setup, "profile": args.profile, "model": args.model,
              "runs_per_task": args.runs, "task_ids": [task.task_id for task in tasks], "server": args.server,
              "bq_project": args.bq_project, "bq_principal": args.bq_principal, "bq_location": args.bq_location,
              "allowed_tools": list(agent.READ_ONLY_TOOLS)}
    if config_path.exists():
        saved = json.loads(config_path.read_text())
        if saved != config:
            raise SystemExit(f"--resume: flags differ from the batch's config.json: {saved} vs {config}")
    else:
        config_path.write_text(json.dumps(config, indent=2))
    snapshot_dir = out_dir / "snapshots"
    snapshot_dir.mkdir(exist_ok=True)
    rows_path, traces_path = out_dir / "runs.jsonl", out_dir / "traces.jsonl"
    rows = read_jsonl(path=rows_path)
    done = {row["run_id"] for row in rows}
    planned = [(task, run_index) for task in tasks for run_index in range(1, args.runs + 1)]
    todo = [(task, run_index) for task, run_index in planned if f"{task.task_id}-{run_index}" not in done]
    print(f"{len(done)} of {len(planned)} runs already saved; {len(todo)} to go"
          + (f", this batch runs up to {args.max_runs}" if args.max_runs else ""), flush=True)

    # An org-level key (not scoped to a workspace) must name the workspace on every request
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    client = AsyncAnthropic(default_headers={"anthropic-workspace-id": workspace_id} if workspace_id else None)

    async def take_snapshot(*, label: str) -> dict:
        async with connect(server_url=args.server, profile=args.profile) as mcp:
            taken = await snapshot.capture(mcp=mcp, allowed_tools=agent.READ_ONLY_TOOLS,
                                           system_prompt=benchmark.SYSTEM_PROMPT, model=args.model)
        stamp_now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (snapshot_dir / f"{stamp_now}__{label}.json").write_text(json.dumps(taken, indent=2, default=str))
        return taken

    # Every batch of a results folder must run against the same context: the first snapshot
    # fixes the hash, and a later batch (resume) or end snapshot that differs fails loudly
    start = await take_snapshot(label="start")
    batch_hash = start["context_hash"]
    first_snapshot = sorted(snapshot_dir.glob("*__start.json"))[0]
    folder_hash = json.loads(first_snapshot.read_text())["context_hash"]
    if batch_hash != folder_hash:
        raise SystemExit(f"context changed since this folder's first batch ({folder_hash[:12]} -> {batch_hash[:12]}); "
                         f"see {snapshot_dir}")
    print(f"context snapshot {batch_hash[:12]} ({len(start['context']['business_concepts'])} concepts)", flush=True)

    failure: Exception | None = None
    for task, run_index in todo[: args.max_runs] if args.max_runs else todo:
        run_id = f"{task.task_id}-{run_index}"
        try:
            # Fresh MCP session per run, so nothing carries over between runs
            async with connect(server_url=args.server, profile=args.profile) as mcp:
                trace = await agent.run_task(
                    client=client, mcp=mcp, model=args.model,
                    system_prompt=benchmark.SYSTEM_PROMPT, task_prompt=task.prompt, prices=prices,
                )
        except Exception as error:
            # Run-level failures are already inside the trace; anything reaching here is the
            # connection or setup, so the run is not saved and a resume redoes it
            failure = error
            print(f"{run_id}: not saved, batch stopped: {type(error).__name__}: {error}", flush=True)
            break
        correct = benchmark.score(answer=trace.answer, expected=task.expected)
        row = run_row(run_id=run_id, benchmark=args.benchmark, profile=args.profile, task=task,
                      run_index=run_index, trace=trace, correct=correct, setup=args.setup, context_hash=batch_hash)
        # Saved the moment the run ends, so a later failure loses nothing
        append_jsonl(path=traces_path, record=trace_record(row=row, trace=trace))
        append_jsonl(path=rows_path, record=row)
        rows.append(row)
        print(f"{run_id}: {'correct' if correct else 'wrong'} answer={trace.answer!r} expected={task.expected!r} "
              f"calls={row['tool_calls']} ${row['cost_usd']:.4f}" + (f" error={trace.error}" if trace.error else ""),
              flush=True)

    end = await take_snapshot(label="end")
    if end["context_hash"] != batch_hash:
        raise SystemExit(f"context changed during the batch ({batch_hash[:12]} -> {end['context_hash'][:12]}); "
                         f"runs in {out_dir} are not one condition, see {snapshot_dir}")

    # Bytes for every saved run still missing them: job history can lag a few seconds behind the query
    await asyncio.sleep(10)
    traces = {record["run_id"]: record for record in read_jsonl(path=traces_path)}
    fill_bytes(rows=rows, traces=traces, args=args)
    rows_path.write_text("".join(json.dumps(row, default=str) + "\n" for row in rows))

    with open(out_dir / "runs.csv", "w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    summary = {"benchmark": args.benchmark, "setup": args.setup, "profile": args.profile, "model": args.model,
               "context_hash": batch_hash,
               "complete": len(rows) == len(planned), "planned_runs": len(planned), **summarize(rows=rows)}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"results: {out_dir}")
    if failure is not None:
        raise SystemExit(f"stopped early; rerun with --resume {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
