# contextflo-benchmarks

Reproducible benchmarks for AI agents that answer questions over a data warehouse
through an MCP server. Every run records whether the answer was right and what it cost
to get there: tokens, warehouse bytes scanned, tool calls, time and dollars.

Everything is a script: download, load, the answer key, the context an agent is given,
the agent loop and the scoring. Nothing is set up by hand, so anyone with a BigQuery
project and an MCP server can rerun it or swap in a different MCP.

## DABstep: does business context help an agent, and does it save money?

Write-up: [AI Agents and Business Context: Does It Help? Does It Save Money?? Let's Find Out!](https://contextflo.com/benchmarks/dabstep-haiku),
with every held-out question and try browsable at the bottom of the post.

- **Data:** [DABstep](https://huggingface.co/datasets/adyen/DABstep) by Adyen and Hugging
  Face (CC BY 4.0): a synthetic payments company with 138k payments, a 1,000-row price list
  and 30 shops. Loaded into BigQuery as-is by [`dabstep/transform.py`](dabstep/transform.py)
  and [`dabstep/load.py`](dabstep/load.py). The data itself is not in this repo.
- **Agent:** Claude Haiku 5.5 (`claude-haiku-5-5`), the same prompt and read-only tools in
  every setup, connected to BigQuery through [Contextflo](https://contextflo.com)'s MCP server.
- **Context:** the docs are DABstep's own manual and payments readme
  ([`dabstep/context/context.json`](dabstep/context/context.json)). The notes are four short
  rules we wrote after reading the agent's wrong answers
  ([`dabstep/context/learned.json`](dabstep/context/learned.json)).

### Results

Case study: 25 hard questions, 3 tries each. The notes were written from these misses.

| What the agent had | Questions right on all 3 tries | Tries right | Cost per try |
|---|---|---|---|
| Only the tables | 3 of 25 | 14 of 75 (19%) | ~6 cents |
| + the docs | 3 of 25 | 12 of 75 (16%) | ~2 cents |
| + note 1: a blank means "any" | 7 of 25 | 23 of 75 (31%) | ~3.5 cents |
| + note 2: add up every price that applies | 14 of 25 | 44 of 75 (59%) | ~1.5 cents |
| + note 3: "monthly" means calendar month | 13 of 25 | 45 of 75 (60%) | ~1.4 cents |
| + note 4: how to answer "which is cheapest" | 14 of 25 | 47 of 75 (63%) | ~1.2 cents |

Held-out test: 42 new hard questions (3 from each of DABstep's 14 question types), run with
notes, docs, prompt, tools, model and scoring frozen first ([`dabstep/freeze.json`](dabstep/freeze.json)).

| What the agent had | Questions right on all 3 tries | Tries right | Cost per try | Cost per right answer |
|---|---|---|---|---|
| Only the tables | 13 of 42 | 50 of 126 (40%) | $0.048 | $0.122 |
| + the docs | 9 of 42 | 45 of 126 (36%) | $0.016 | $0.045 |
| + the four notes | 31 of 42 | 96 of 126 (76%) | $0.013 | $0.017 |

The 3 tries per question measure repeatability; they are not independent tests, so
"right on all 3 tries" is the stricter number.

### How answers are scored

DABstep keeps its answers secret, but its leaderboard publishes every submission and
whether the official scorer accepted it. So:

1. **Scoreboard verdict (primary).** If the exact answer was submitted to the leaderboard
   before, we use the majority verdict on it.
2. **Strict check (fallback).** Otherwise, the official scorer against the most precise
   answer the leaderboard accepted for that question, with signs and labels required to
   match. These are reported as unverified: on the held-out test, 12 of 126 tries with
   only the tables, 19 with the docs, 2 with docs and notes.

Our first scoring used a consensus answer key alone and was too generous (77% instead of
63%); `harness/rescore.py` is the re-score. Every question where the scoring views
disagree was reviewed by hand, and no verdict was changed by hand.

### Where everything is

| Folder | What | Full trace |
|---|---|---|
| `results/20261110T064950Z__…benchmark-raw…` | Case study, only the tables | [24 MB](https://assets.contextflo.com/benchmarks/dabstep-haiku/traces/v1/20261110T064950Z__dabstep__benchmark-raw__claude-haiku-5-5.jsonl) |
| `results/20261110T080305Z__…benchmark-context…` | Case study, + docs | [13 MB](https://assets.contextflo.com/benchmarks/dabstep-haiku/traces/v1/20261110T080305Z__dabstep__benchmark-context__claude-haiku-5-5.jsonl) |
| `results/20261111T183039Z__…benchmark-learned…` | Case study, + note 1 | [18 MB](https://assets.contextflo.com/benchmarks/dabstep-haiku/traces/v1/20261111T183039Z__dabstep__benchmark-learned__claude-haiku-5-5.jsonl) |
| `results/20261111T211207Z__…benchmark-learned…` | Case study, + notes 1-2 | [11 MB](https://assets.contextflo.com/benchmarks/dabstep-haiku/traces/v1/20261111T211207Z__dabstep__benchmark-learned__claude-haiku-5-5.jsonl) |
| `results/20261111T220442Z__…benchmark-learned…` | Case study, + notes 1-3 | [11 MB](https://assets.contextflo.com/benchmarks/dabstep-haiku/traces/v1/20261111T220442Z__dabstep__benchmark-learned__claude-haiku-5-5.jsonl) |
| `results/20261111T230126Z__…benchmark-learned…` | Case study, + notes 1-4 | [10 MB](https://assets.contextflo.com/benchmarks/dabstep-haiku/traces/v1/20261111T230126Z__dabstep__benchmark-learned__claude-haiku-5-5.jsonl) |
| `results/rescore/` | Case study re-scored against the scoreboard | |
| `results/20261117T092622Z__…heldout-tables…` | Held-out, only the tables | [36 MB](https://assets.contextflo.com/benchmarks/dabstep-haiku/traces/v1/20261117T092622Z__dabstep__heldout-tables__claude-haiku-5-5.jsonl) |
| `results/20261117T110112Z__…heldout-docs__…` | Held-out, + docs | [19 MB](https://assets.contextflo.com/benchmarks/dabstep-haiku/traces/v1/20261117T110112Z__dabstep__heldout-docs__claude-haiku-5-5.jsonl) |
| `results/20261117T081655Z__…heldout-docs-notes…` | Held-out, + docs and notes | [17 MB](https://assets.contextflo.com/benchmarks/dabstep-haiku/traces/v1/20261117T081655Z__dabstep__heldout-docs-notes__claude-haiku-5-5.jsonl) |
| `results/heldout-report/` | Held-out scored: `answers.csv`, `summary.json`, one file per question | |

Each run folder has `runs.csv` / `runs.jsonl` (one row per try: answer, verdict, tokens,
bytes, cost) and `summary.json`. Held-out folders also have `config.json` and
`snapshots/`: everything the agent could see, captured at the start and end of the batch,
with matching hashes.

The full traces (every tool call with its exact input and output, and the agent's final
reply) are too large for git, so they are hosted separately, linked above. Download one
into its run folder as `traces.jsonl` to rerun the reports (`harness.heldout_report`,
`harness.rescore`, `harness.export_receipts` read them from there).

Our GCP project and service account are replaced with `<gcp-project>` and
`<reader-service-account>`; nothing else is edited.

## How a run works

The harness is the agent. For each task it opens a fresh MCP session, offers the model a
read-only subset of the server's tools, calls the Anthropic Messages API, runs each tool
call against the MCP server itself and feeds the result back until the model answers.
Since every tool result passes through the harness, each one can be measured.

Per run (`runs.csv`):

| Column | Meaning |
|---|---|
| `correct` | Scored with the benchmark's own scorer |
| `sql_writing_tokens` | Model output, plus schema and context lookups, plus results of every query except the final one (samples, retries) |
| `results_reading_tokens` | Result of the final query, the rows the answer is read from |
| `bytes_exploration` / `bytes_final` | BigQuery bytes processed, split the same way |
| `tool_calls`, `wall_seconds` | |
| `input_tokens` ... `cache_read_tokens` | What the API billed, history re-reads included |
| `cost_usd` | Billed tokens at list price (`harness/pricing.json`) |

Choices worth knowing:

- **Read-only tools.** An agent that can write context (descriptions, saved queries)
  would change the org it runs against for every later run. Only lookup and query tools
  are offered (`READ_ONLY_TOOLS` in `harness/agent.py`).
- **The final query** is the last successful query before the answer. Everything before
  it is exploration.
- **Prompt caching is on**, the way Claude clients use it (system prompt, tool list and
  conversation history). Leaving it off would overstate the cost of every run.
- **Bytes come from BigQuery's job history**, not from the MCP server: after a batch the
  harness reads `INFORMATION_SCHEMA.JOBS_BY_PROJECT` for the service account the server
  queries as, and gives each job to the tool call whose time window contains it. Runs
  execute one at a time so windows never overlap. Jobs outside every window are reported
  as `bytes_unattributed`, never dropped.
- **A run that errors counts as wrong**, and the error is kept in the row.

## Reproducing it

Setup, once:

```bash
uv sync
uv run python dabstep/download.py          # pinned DABstep revision from Hugging Face
uv run python dabstep/transform.py         # raw files -> NDJSON + BigQuery schemas
uv run python dabstep/load.py --project <gcp-project>
uv run python -m dabstep.ground_truth      # answer key, see dabstep/README.md
```

Then connect the `dabstep` dataset to your MCP server with a read-only service account,
one org per context state (see `dabstep/README.md`), and log each one in:

```bash
uv run python -m harness.login --profile <name>   # prints a consent URL, open it as that user
uv run python -m dabstep.apply_context --profile <docs-profile> --project <gcp-project> --yes
uv run python -m dabstep.apply_context --profile <notes-profile> --project <gcp-project> --yes
uv run python -m dabstep.apply_learned --profile <notes-profile> --rule <rule-id> --yes   # once per note, in order
```

The held-out test, one batch per setup (needs `ANTHROPIC_API_KEY`, and Application Default
Credentials that can read the project's BigQuery job history):

```bash
uv run python -m dabstep.select_heldout --per-family 3 --min-support 20   # writes dabstep/heldout.txt
uv run python -m harness.run --benchmark dabstep --setup heldout-tables --profile <tables-profile> \
    --model claude-haiku-5-5 --task-file dabstep/heldout.txt --runs 3 \
    --bq-project <gcp-project> --bq-principal <reader-service-account-email>
# same again with --setup heldout-docs and --setup heldout-docs-notes and their profiles

uv run python -m harness.heldout_report --out results/heldout-report \
    --setup heldout-tables=results/<folder> --setup heldout-docs=results/<folder> \
    --setup heldout-docs-notes=results/<folder>
```

The case study is the same with the 25 tasks in `dabstep/tasks.txt` (the default task
list), one run per context state, then `harness.rescore` over the six folders. The
receipts in the blog post come from `harness.export_receipts`.

The full held-out test cost about $10 in model tokens.

## Adding a benchmark

A benchmark is a package with a `benchmark.py` exposing `NAME`, `SYSTEM_PROMPT`,
`load_tasks()` and `score()`. See `dabstep/benchmark.py`.

## License

Code: Apache-2.0 (`LICENSE`). Each benchmark's data keeps its own license; DABstep is
CC BY 4.0, (c) Adyen, and its scorer is vendored unchanged with attribution.
