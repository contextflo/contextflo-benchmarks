"""Turn the raw DABstep files into BigQuery-ready NDJSON plus a schema per table.

Transforms are type-only on purpose: values are never cleaned, recoded or joined, so
the warehouse holds exactly what the benchmark ships. Decisions worth knowing:

- fees.json list fields (account_type, aci, merchant_category_code) become REPEATED
  columns. In a fee rule both NULL and an empty list mean "applies to every value";
  they are kept as-is (NULL scalar, empty array) rather than normalized, because
  telling them apart is part of what the tasks test.
- fees.intracountry is 0.0 / 1.0 / null in the source: stored as INT64 (0 / 1 / NULL).
- payments.csv booleans arrive as "True"/"False": parsed to BOOL.
- The two small CSVs carry a pandas index as an unnamed first column: dropped.
- manual.md and payments-readme.md are documentation, not tables. They are the
  "with context" input (see apply_context.py) and never go into the warehouse.
"""

import csv
import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
RAW_DIR = DATA_DIR / "raw" / "context"
OUT_DIR = DATA_DIR / "bigquery"

PAYMENTS_SCHEMA = [
    ("psp_reference", "INT64"),
    ("merchant", "STRING"),
    ("card_scheme", "STRING"),
    ("year", "INT64"),
    ("hour_of_day", "INT64"),
    ("minute_of_hour", "INT64"),
    ("day_of_year", "INT64"),
    ("is_credit", "BOOL"),
    ("eur_amount", "FLOAT64"),
    ("ip_country", "STRING"),
    ("issuing_country", "STRING"),
    ("device_type", "STRING"),
    ("ip_address", "STRING"),
    ("email_address", "STRING"),
    ("card_number", "STRING"),
    ("shopper_interaction", "STRING"),
    ("card_bin", "INT64"),
    ("has_fraudulent_dispute", "BOOL"),
    ("is_refused_by_adyen", "BOOL"),
    ("aci", "STRING"),
    ("acquirer_country", "STRING"),
]

FEES_SCHEMA = [
    ("ID", "INT64", "REQUIRED"),
    ("card_scheme", "STRING", "NULLABLE"),
    ("account_type", "STRING", "REPEATED"),
    ("capture_delay", "STRING", "NULLABLE"),
    ("monthly_fraud_level", "STRING", "NULLABLE"),
    ("monthly_volume", "STRING", "NULLABLE"),
    ("merchant_category_code", "INT64", "REPEATED"),
    ("is_credit", "BOOL", "NULLABLE"),
    ("aci", "STRING", "REPEATED"),
    ("fixed_amount", "FLOAT64", "NULLABLE"),
    ("rate", "INT64", "NULLABLE"),
    ("intracountry", "INT64", "NULLABLE"),
]

MERCHANT_DATA_SCHEMA = [
    ("merchant", "STRING", "REQUIRED"),
    ("capture_delay", "STRING", "NULLABLE"),
    ("acquirer", "STRING", "REPEATED"),
    ("merchant_category_code", "INT64", "NULLABLE"),
    ("account_type", "STRING", "NULLABLE"),
]

MCC_SCHEMA = [("mcc", "INT64"), ("description", "STRING")]
ACQUIRER_COUNTRIES_SCHEMA = [("acquirer", "STRING"), ("country_code", "STRING")]


class TransformError(Exception):
    pass


def parse_value(*, raw: str, bq_type: str, column: str):
    if raw == "":
        return None
    if bq_type == "INT64":
        return int(raw)
    if bq_type == "FLOAT64":
        return float(raw)
    if bq_type == "BOOL":
        if raw not in ("True", "False"):
            raise TransformError(f"{column}: expected True/False, found {raw!r}")
        return raw == "True"
    return raw


def schema_json(*, columns: list[tuple]) -> list[dict]:
    return [
        {"name": column[0], "type": column[1], "mode": column[2] if len(column) > 2 else "NULLABLE"}
        for column in columns
    ]


def write_table(*, name: str, rows: list[dict], columns: list[tuple]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / f"{name}.ndjson", "w") as out:
        for row in rows:
            out.write(json.dumps(row) + "\n")
    with open(OUT_DIR / f"{name}.schema.json", "w") as out:
        json.dump(schema_json(columns=columns), out, indent=2)
    print(f"{name}: {len(rows)} rows")


def read_csv_table(*, file_name: str, columns: list[tuple], drop_index: bool) -> list[dict]:
    with open(RAW_DIR / file_name, newline="") as source:
        reader = csv.DictReader(source)
        expected = [column[0] for column in columns]
        found = [field for field in (reader.fieldnames or []) if not (drop_index and field == "")]
        if found != expected:
            raise TransformError(f"{file_name}: expected columns {expected}, found {found}")
        return [
            {name: parse_value(raw=row[name], bq_type=bq_type, column=name) for name, bq_type in columns}
            for row in reader
        ]


def transform_fees() -> list[dict]:
    rules = json.loads((RAW_DIR / "fees.json").read_text())
    rows = []
    for rule in rules:
        intracountry = rule["intracountry"]
        if intracountry not in (None, 0, 1):
            raise TransformError(f"fee {rule['ID']}: intracountry must be 0, 1 or null, found {intracountry!r}")
        rows.append({**rule, "intracountry": None if intracountry is None else int(intracountry)})
    expected = {column[0] for column in FEES_SCHEMA}
    for row in rows:
        if set(row) != expected:
            raise TransformError(f"fee {row.get('ID')}: unexpected fields {sorted(set(row) ^ expected)}")
    return rows


def main() -> None:
    write_table(
        name="payments",
        rows=read_csv_table(file_name="payments.csv", columns=PAYMENTS_SCHEMA, drop_index=False),
        columns=PAYMENTS_SCHEMA,
    )
    write_table(name="fees", rows=transform_fees(), columns=FEES_SCHEMA)
    write_table(
        name="merchant_data",
        rows=json.loads((RAW_DIR / "merchant_data.json").read_text()),
        columns=MERCHANT_DATA_SCHEMA,
    )
    write_table(
        name="merchant_category_codes",
        rows=read_csv_table(file_name="merchant_category_codes.csv", columns=MCC_SCHEMA, drop_index=True),
        columns=MCC_SCHEMA,
    )
    write_table(
        name="acquirer_countries",
        rows=read_csv_table(file_name="acquirer_countries.csv", columns=ACQUIRER_COUNTRIES_SCHEMA, drop_index=True),
        columns=ACQUIRER_COUNTRIES_SCHEMA,
    )


if __name__ == "__main__":
    sys.exit(main())
