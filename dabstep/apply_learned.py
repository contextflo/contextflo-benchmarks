"""Add one learned rule (context/learned.json) to an MCP profile's org, as a business concept.

Usage: uv run python -m dabstep.apply_learned --profile <learned-profile> --rule <rule-id> --yes

The learned org climbs one rule at a time: add a rule, run the suite, add the next. Rules
must go in the order they are listed, and each only once (the MCP does not deduplicate
concepts), so the script keeps a ledger of what it added and refuses anything out of order.
"""

import argparse
import asyncio
import json
from pathlib import Path

from harness.login import DEFAULT_SERVER
from harness.mcp_connection import connect

LEARNED_FILE = Path(__file__).parent / "context" / "learned.json"
LEDGER_DIR = Path.home() / ".config" / "contextflo-benchmarks"
WHY = "Benchmark setup: adding a rule the DABstep documentation leaves unwritten, found from the docs-only misses."


def tool_text(*, result) -> str:
    if result.is_error:
        raise SystemExit(f"tool call failed: {result.content[0].text if result.content else 'no detail'}")
    return result.content[0].text


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--rule", required=True, help="rule id from context/learned.json")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--yes", action="store_true", help="confirm this is the learned org")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("this writes context into the profile's org; rerun with --yes if it is the learned org")

    rules = json.loads(LEARNED_FILE.read_text())["rules"]
    ids = [rule["id"] for rule in rules]
    if args.rule not in ids:
        raise SystemExit(f"unknown rule {args.rule!r}; known: {ids}")
    position = ids.index(args.rule)

    # The MCP fetches concepts by id only, so the order is kept in a local ledger per profile
    ledger_path = LEDGER_DIR / f"{args.profile}.learned.json"
    applied = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    if applied != ids[:position]:
        raise SystemExit(f"applied so far: {applied}; next in order is {ids[len(applied)]!r}, not {args.rule!r}")

    rule = rules[position]
    async with connect(server_url=args.server, profile=args.profile) as client:
        tool_text(result=await client.call_tool("create-business-concept", {
            "name": rule["name"], "description": rule["description"], "userPrompt": WHY, "llmReasoning": WHY,
        }))
    ledger_path.write_text(json.dumps([*applied, rule["id"]]))
    print(f"added rule {position + 1}/{len(rules)}: {rule['id']}")


if __name__ == "__main__":
    asyncio.run(main())
