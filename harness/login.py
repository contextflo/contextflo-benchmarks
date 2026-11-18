"""Log a profile in once and print what it can see.

Usage: uv run python -m harness.login --profile <name> [--server https://mcp.contextflo.com/mcp]

Opens the browser for consent the first time; later runs reuse the stored tokens.
Prints the tool list, so you can confirm the token landed on the org you meant.
"""

import argparse
import asyncio

from harness.mcp_connection import connect

DEFAULT_SERVER = "https://mcp.contextflo.com/mcp"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--server", default=DEFAULT_SERVER)
    args = parser.parse_args()

    async with connect(server_url=args.server, profile=args.profile) as client:
        tools = await client.list_tools()
        print(f"[{args.profile}] connected, {len(tools.tools)} tools: {', '.join(tool.name for tool in tools.tools)}")


if __name__ == "__main__":
    asyncio.run(main())
