"""Snapshot everything an agent could see through the MCP, so each result can be checked later.

Taken at the start and at the end of every batch (harness.run). It records:
  - the server's name, version and instructions (sent at connect)
  - the tools the agent was offered (the allowlist), with their descriptions and schemas
  - getOrgInfo as the agent would read it
  - every table the org exposes, and its full table and column context
  - every business concept (the docs' concepts and the notes), in full
  - the system prompt, the model, and a hash of every harness and benchmark source file

The context part is hashed. The batch fails if the start and end hashes differ, so a
result can never mix two context states. Snapshot calls run outside any run's time window,
so they never count toward a run's bytes or tokens.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from mcp import Client

WHY = "Benchmark snapshot: recording the context state this batch runs against."
REPO = Path(__file__).parent.parent
SOURCE_GLOBS = ("harness/*.py", "harness/pricing.json", "dabstep/*.py", "dabstep/context/*.json",
                "dabstep/tasks.txt", "dabstep/heldout.txt", "dabstep/heldout.json", "dabstep/freeze.json")


def tool_text(*, result) -> str:
    if result.is_error:
        raise RuntimeError(f"snapshot tool call failed: {result.content[0].text if result.content else 'no detail'}")
    return result.content[0].text


def source_hashes() -> dict[str, str]:
    hashes = {}
    for pattern in SOURCE_GLOBS:
        for path in sorted(REPO.glob(pattern)):
            hashes[str(path.relative_to(REPO))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def context_hash(*, context: dict) -> str:
    return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()


async def capture(*, mcp: Client, allowed_tools: tuple[str, ...], system_prompt: str, model: str) -> dict:
    tools = [
        {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        for tool in (await mcp.list_tools()).tools if tool.name in allowed_tools
    ]
    org_info = json.loads(tool_text(result=await mcp.call_tool("getOrgInfo", {"userPrompt": WHY})))
    listed = json.loads(tool_text(result=await mcp.call_tool("list-tables", {"userPrompt": WHY, "llmReasoning": WHY})))
    names = sorted(table["fullyQualifiedName"] for table in listed["tables"])
    table_context = json.loads(tool_text(result=await mcp.call_tool("get-table-context", {
        "fullyQualifiedNames": names, "userPrompt": WHY, "llmReasoning": WHY})))
    related = json.loads(tool_text(result=await mcp.call_tool("find-related-resources", {
        "userPrompt": WHY, "llmReasoning": WHY})))
    concepts = sorted(related["businessConcepts"], key=lambda concept: concept["name"])
    expected_concepts = org_info["resources"]["business_concepts"]
    if len(concepts) != expected_concepts:
        raise RuntimeError(f"snapshot found {len(concepts)} business concepts, org reports {expected_concepts}")
    if org_info["resources"]["metrics"] or related.get("metrics"):
        raise RuntimeError("org has metrics; the snapshot does not capture them yet")

    context = {
        "server": {"name": mcp.server_info.name, "version": mcp.server_info.version,
                   "description": mcp.server_info.description, "instructions": mcp.instructions},
        "tools": tools,
        "org_info": org_info,
        "tables": listed,
        "table_context": table_context,
        "business_concepts": concepts,
        "system_prompt": system_prompt,
        "model": model,
    }
    return {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "context_hash": context_hash(context=context),
        "context": context,
        "source_hashes": source_hashes(),
    }
