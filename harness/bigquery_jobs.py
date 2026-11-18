"""Bytes scanned per run, from BigQuery's own job history.

The MCP server does not report bytes, so after a batch the harness reads
INFORMATION_SCHEMA.JOBS_BY_PROJECT for the service account the MCP server queries as,
and gives each job to the tool call whose time window contains it. Bytes come out by
the role of that call: "final" (the query the answer came from), "exploration" (every
other execute-query) and "context" (jobs run during schema/context lookups, if any).
Jobs that fall in no call window are reported as "unattributed", never dropped.

This only works when runs against that service account do not overlap in time, so the
runner executes runs one at a time.

Reading JOBS_BY_PROJECT needs bigquery.jobs.listAll on the project (e.g. BigQuery
Resource Viewer). It uses Application Default Credentials.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from google.cloud import bigquery

# Job creation can trail the tool call's start by a moment, and the MCP may still be
# finishing a job when the call returns; a small margin on each side.
WINDOW_MARGIN = timedelta(seconds=2)


@dataclass(frozen=True)
class Job:
    job_id: str
    created_at: datetime
    bytes_processed: int
    bytes_billed: int


def fetch_jobs(*, project: str, location: str, principal: str, since: datetime, until: datetime) -> list[Job]:
    client = bigquery.Client(project=project)
    sql = f"""
        SELECT job_id, creation_time, IFNULL(total_bytes_processed, 0) AS bytes_processed,
               IFNULL(total_bytes_billed, 0) AS bytes_billed
        FROM `region-{location.lower()}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
        WHERE user_email = @principal
          AND creation_time BETWEEN @since AND @until
          AND job_type = 'QUERY'
        ORDER BY creation_time
    """
    config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("principal", "STRING", principal),
        bigquery.ScalarQueryParameter("since", "TIMESTAMP", since - WINDOW_MARGIN),
        bigquery.ScalarQueryParameter("until", "TIMESTAMP", until + WINDOW_MARGIN),
    ])
    rows = client.query(sql, job_config=config).result()
    return [
        Job(job_id=row.job_id, created_at=row.creation_time, bytes_processed=row.bytes_processed, bytes_billed=row.bytes_billed)
        for row in rows
    ]


def attribute(*, jobs: list[Job], calls: list) -> dict[str, int]:
    """Bytes by call role, as bytes_<role> (processed) and billed_<role> (what BigQuery bills:
    at least 10 MB per query, rounded up). `calls` are harness.agent.ToolCall with role set."""
    roles = ("final", "exploration", "context", "unattributed")
    totals = {f"{kind}_{role}": 0 for kind in ("bytes", "billed") for role in roles}
    for job in jobs:
        owner = next(
            (call for call in calls if call.started_at - WINDOW_MARGIN <= job.created_at <= call.ended_at + WINDOW_MARGIN),
            None,
        )
        role = owner.role if owner is not None else "unattributed"
        totals[f"bytes_{role}"] += job.bytes_processed
        totals[f"billed_{role}"] += job.bytes_billed
    return totals
