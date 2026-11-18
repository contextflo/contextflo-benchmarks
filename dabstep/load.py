"""Load the transformed DABstep tables into a BigQuery dataset with the bq CLI.

Usage: python load.py --project <gcp-project> [--dataset dabstep] [--location US]

Creates the dataset if missing and replaces each table, so a rerun always leaves the
warehouse matching the pinned source. Needs an authenticated bq CLI.
"""

import argparse
import subprocess
import sys
from pathlib import Path

BIGQUERY_DIR = Path(__file__).parent / "data" / "bigquery"
TABLES = ["payments", "fees", "merchant_data", "merchant_category_codes", "acquirer_countries"]
DESCRIPTION = "DABstep benchmark data (adyen/DABstep, CC BY 4.0). Loaded by contextflo-benchmarks/dabstep/load.py."


def run(*, args: list[str]) -> subprocess.CompletedProcess:
    print("$ " + " ".join(args))
    return subprocess.run(args, capture_output=True, text=True)


def ensure_dataset(*, project: str, dataset: str, location: str) -> None:
    shown = run(args=["bq", "show", "--format=none", f"{project}:{dataset}"])
    if shown.returncode == 0:
        return
    created = run(args=["bq", f"--location={location}", "mk", "--dataset", f"--description={DESCRIPTION}", f"{project}:{dataset}"])
    if created.returncode != 0:
        raise SystemExit(f"could not create dataset {project}:{dataset}: {created.stderr.strip()}")


def load_table(*, project: str, dataset: str, table: str) -> None:
    loaded = run(args=[
        "bq", "load", "--replace", "--source_format=NEWLINE_DELIMITED_JSON",
        f"{project}:{dataset}.{table}",
        str(BIGQUERY_DIR / f"{table}.ndjson"),
        str(BIGQUERY_DIR / f"{table}.schema.json"),
    ])
    if loaded.returncode != 0:
        raise SystemExit(f"could not load {table}: {loaded.stderr.strip() or loaded.stdout.strip()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", default="dabstep")
    parser.add_argument("--location", default="US")
    args = parser.parse_args()

    missing = [table for table in TABLES if not (BIGQUERY_DIR / f"{table}.ndjson").exists()]
    if missing:
        raise SystemExit(f"run transform.py first, missing: {missing}")

    ensure_dataset(project=args.project, dataset=args.dataset, location=args.location)
    for table in TABLES:
        load_table(project=args.project, dataset=args.dataset, table=table)
    print(f"loaded {len(TABLES)} tables into {args.project}:{args.dataset}")


if __name__ == "__main__":
    sys.exit(main())
