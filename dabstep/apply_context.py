"""Write the 'with context' state (context/context.json) into an MCP profile's org.

Usage: uv run python -m dabstep.apply_context --profile <context-profile> --project <gcp-project> --yes

Run it once, against the WITH-context org only. It sets table and column descriptions
with set-table-context and creates one business concept per entry. Concepts are not
deduplicated by the MCP, so a second run creates duplicates: the script refuses if the
tables already carry descriptions.
"""

import argparse
import asyncio
import json
from pathlib import Path

from harness.login import DEFAULT_SERVER
from harness.mcp_connection import connect

CONTEXT_FILE = Path(__file__).parent / "context" / "context.json"
WHY = "Benchmark setup: loading DABstep's own documentation as the 'with context' state."


def tool_text(*, result) -> str:
    if result.is_error:
        raise SystemExit(f"tool call failed: {result.content[0].text if result.content else 'no detail'}")
    return result.content[0].text


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True, help="GCP project that holds the dataset")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--yes", action="store_true", help="confirm this is the WITH-context org")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("this writes context into the profile's org; rerun with --yes if it is the with-context org")

    context = json.loads(CONTEXT_FILE.read_text())
    prefix = f"{args.project}.{context['dataset']}"

    async with connect(server_url=args.server, profile=args.profile) as client:
        listed = json.loads(tool_text(result=await client.call_tool("list-tables", {"userPrompt": WHY, "llmReasoning": WHY})))
        tables = {table["fullyQualifiedName"]: table for table in listed["tables"]}
        described = [name for name, table in tables.items() if table["description"]]
        if described:
            raise SystemExit(f"context already applied (tables with descriptions: {described}); not writing twice")

        for table in context["tables"]:
            name = f"{prefix}.{table['table']}"
            if name not in tables:
                raise SystemExit(f"{name} is not synced in this org; found {sorted(tables)}")
            tool_text(result=await client.call_tool("set-table-context", {
                "dataSourceType": "BIGQUERY",
                "fullyQualifiedName": name,
                "description": table["description"],
                "columns": [{"name": column, "description": text} for column, text in table["columns"].items()],
                "userPrompt": WHY,
                "llmReasoning": WHY,
            }))
            print(f"described {name} ({len(table['columns'])} columns)")

        for concept in context["concepts"]:
            tool_text(result=await client.call_tool("create-business-concept", {
                "name": concept["name"],
                "description": concept["description"],
                "userPrompt": WHY,
                "llmReasoning": WHY,
            }))
            print(f"concept: {concept['name']}")


if __name__ == "__main__":
    asyncio.run(main())
