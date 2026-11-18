# DABstep

[DABstep](https://huggingface.co/datasets/adyen/DABstep) (Adyen, CC BY 4.0) is a set of
450 data analysis tasks over a synthetic payments dataset: 138k transactions, 1,000 fee
rules, 30 merchants. The hard tasks need multi-step reasoning across tables and the fee
rules in its manual, e.g. "what are the total fees Belles_cookbook_store paid on the 12th
day of 2023".

## Data

`download.py` fetches a pinned revision (`REVISION`), `transform.py` turns it into
BigQuery tables, `load.py` loads them:

| Table | Rows | From |
|---|---|---|
| `payments` | 138,236 | `payments.csv` |
| `fees` | 1,000 | `fees.json` |
| `merchant_data` | 30 | `merchant_data.json` |
| `merchant_category_codes` | 769 | `merchant_category_codes.csv` |
| `acquirer_countries` | 8 | `acquirer_countries.csv` |

Transforms change types only, never values. Fee rule list fields become REPEATED
columns and keep empty lists as empty lists. `fees.intracountry` (0.0 / 1.0 / null in
the source) is stored as INT64. `manual.md` and `payments-readme.md` are documentation,
not tables.

## Answer key

DABstep publishes answers only for its 10 dev tasks; the rest are hidden behind the
leaderboard. But the leaderboard also publishes every submission's per-task result: the
submitted answer and whether the official scorer accepted it against the hidden answer.
An accepted answer is a known-correct answer.

The scorer is tolerant, so one accepted answer may only be close to the hidden one.
`ground_truth.py` therefore keeps a task only when at least 5 submissions were accepted
on it and every accepted answer matches the chosen one (the most common) under the
scorer. Checked against the dev split, where the real answers are known, the consensus
matched on every dev task that had one (6 of 6).

## Tasks

`select_tasks.py` picks 25 hard tasks into `tasks.txt`: one per question type, round
robin, ordered by a hash of the task id so the pick is stable. Tasks whose answer is
"Not Applicable" or an empty list are left out, since an agent can score on those by
giving up.

## Context states

The comparison is the same data with and without context, so it needs two MCP
identities, each on its own org with the same five tables connected:

- **Without context**: tables connected, nothing else.
- **With context**: `apply_context.py` writes `context/context.json` as table and
  column descriptions plus business concepts. That file is DABstep's own documentation,
  copied verbatim and split into those shapes; its `_about` lists the few lines that are
  not from the manual.

Two orgs instead of one org reset between runs: a reset that missed something would leak
context into the "without" runs, and two orgs can be inspected side by side.

## Scorer

`scorer.py` is DABstep's official scorer, unchanged. Note its quirk: a leading minus sign
is ignored, so `-0.95` and `0.95` score as equal. That is the leaderboard's rule, so it
is ours too.
