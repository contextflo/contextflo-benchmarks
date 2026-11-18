"""Run one task: a Claude model answering through an MCP server's tools.

The harness is the agent loop. It lists the server's tools, calls the Messages API, runs
each tool call against the MCP server itself and feeds the result back, until the model
stops calling tools. Because every tool result passes through here, it can be measured:

- tokens of each tool result, by kind (schema/context lookups vs query results)
- which execute-query call was the final one (the last successful query before the
  answer) and which were exploration (sampling, retries, dead ends)
- the time window of each call, so warehouse jobs can be matched to it afterwards

Only tools on the allowlist are offered. The default is Contextflo's read-only set: an
agent that could write context (set-table-context, save-query-pattern, ...) would
contaminate the org it runs against for every later run.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import anthropic
from anthropic import AsyncAnthropic
from mcp import Client
from mcp.shared.exceptions import MCPError

from harness import pricing


class InfraError(Exception):
    """The connection failed (MCP session lost, API unreachable), not the agent. The run is
    not a data point: the runner stops the batch without saving it, and a resume redoes it."""

READ_ONLY_TOOLS = (
    "getOrgInfo",
    "find-related-resources",
    "list-tables",
    "get-table-context",
    "execute-query",
    "get-saved-query",
    "get-metric",
    "get-business-concept",
)
QUERY_TOOL = "execute-query"
MAX_TURNS = 40
MAX_OUTPUT_TOKENS = 8192
FINAL_ANSWER = re.compile(r"FINAL ANSWER:\s*(.+)", re.IGNORECASE)


@dataclass
class ToolCall:
    name: str
    arguments: dict
    started_at: datetime
    ended_at: datetime
    is_error: bool
    result_text: str
    result_tokens: int
    # Set after the run: "final" or "exploration" for execute-query, "context" otherwise
    role: str = ""


@dataclass
class RunTrace:
    model: str
    started_at: datetime
    ended_at: datetime | None = None
    turns: int = 0
    stop_reason: str | None = None
    answer: str | None = None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    # Summed per request, so a long-context price tier applies only to the requests that cross it
    cost_usd: float = 0.0
    tool_calls: list[ToolCall] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)


def now() -> datetime:
    return datetime.now(timezone.utc)


def anthropic_tools(*, mcp_tools: list, allowed: tuple[str, ...]) -> list[dict]:
    offered = [
        {"name": tool.name, "description": tool.description or "", "input_schema": tool.input_schema}
        for tool in mcp_tools
        if tool.name in allowed
    ]
    missing = set(allowed) - {tool["name"] for tool in offered}
    if missing:
        raise RuntimeError(f"server does not offer allowlisted tools {sorted(missing)}")
    # Cache the (long, unchanging) tool list like an MCP client does
    offered[-1] = {**offered[-1], "cache_control": {"type": "ephemeral"}}
    return offered


def result_text(*, result) -> str:
    return "\n".join(block.text for block in result.content if getattr(block, "type", None) == "text")


async def count_tokens(*, client: AsyncAnthropic, model: str, text: str) -> int:
    if not text:
        return 0
    counted = await client.messages.count_tokens(model=model, messages=[{"role": "user", "content": text}])
    return counted.input_tokens


def classify_calls(*, calls: list[ToolCall]) -> None:
    successful_queries = [index for index, call in enumerate(calls) if call.name == QUERY_TOOL and not call.is_error]
    final_index = successful_queries[-1] if successful_queries else None
    for index, call in enumerate(calls):
        if call.name != QUERY_TOOL:
            call.role = "context"
        elif index == final_index:
            call.role = "final"
        else:
            call.role = "exploration"


async def run_task(
    *,
    client: AsyncAnthropic,
    mcp: Client,
    model: str,
    system_prompt: str,
    task_prompt: str,
    prices: dict,
    allowed_tools: tuple[str, ...] = READ_ONLY_TOOLS,
) -> RunTrace:
    trace = RunTrace(model=model, started_at=now())
    tools = anthropic_tools(mcp_tools=(await mcp.list_tools()).tools, allowed=allowed_tools)
    # Pass the server's own instructions on, as Claude clients do
    system_text = system_prompt if not mcp.instructions else f"{system_prompt}\n\n{mcp.instructions}"
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict] = [{"role": "user", "content": task_prompt}]

    try:
        while trace.turns < MAX_TURNS:
            trace.turns += 1
            response = await client.messages.create(
                model=model, max_tokens=MAX_OUTPUT_TOKENS, system=system, tools=tools,
                messages=with_history_cache(messages=messages),
            )
            usage = response.usage
            trace.input_tokens += usage.input_tokens
            trace.output_tokens += usage.output_tokens
            trace.cache_write_tokens += usage.cache_creation_input_tokens or 0
            trace.cache_read_tokens += usage.cache_read_input_tokens or 0
            trace.cost_usd += pricing.request_cost(
                prices=prices,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
            )
            trace.stop_reason = response.stop_reason

            assistant_content = [block.model_dump(mode="json", exclude_none=True) for block in response.content]
            messages.append({"role": "assistant", "content": assistant_content})
            trace.transcript.append({"role": "assistant", "content": assistant_content})

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                text = "\n".join(block.text for block in response.content if block.type == "text")
                matches = FINAL_ANSWER.findall(text)
                trace.answer = matches[-1].strip() if matches else None
                break

            results = []
            for tool_use in tool_uses:
                started = now()
                result = await mcp.call_tool(tool_use.name, tool_use.input)
                ended = now()
                text = result_text(result=result)
                trace.tool_calls.append(ToolCall(
                    name=tool_use.name,
                    arguments=tool_use.input,
                    started_at=started,
                    ended_at=ended,
                    is_error=bool(result.is_error),
                    result_text=text,
                    result_tokens=await count_tokens(client=client, model=model, text=text),
                ))
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": text,
                    "is_error": bool(result.is_error),
                })
            messages.append({"role": "user", "content": results})
            trace.transcript.append({"role": "user", "content": results})
        else:
            trace.error = f"stopped after {MAX_TURNS} turns without an answer"
    except (MCPError, anthropic.APIConnectionError) as error:
        # Tool failures come back as is_error results; a protocol-level MCPError means the
        # session itself is gone
        raise InfraError(f"{type(error).__name__}: {error}") from error
    except anthropic.APIStatusError as error:
        # Rejected on the very first request = a setup problem (key, workspace, model id,
        # request shape) that would fail every run the same way: stop the batch rather than
        # record it as a wrong answer per task. Later in a run it is a real outcome.
        if trace.turns == 1 and not trace.tool_calls and error.status_code in (400, 401, 403, 404):
            raise
        trace.error = f"{type(error).__name__}: {error}"
    except Exception as error:
        # A failed run is a data point (counted as wrong), not a reason to stop the batch
        trace.error = f"{type(error).__name__}: {error}"

    trace.ended_at = now()
    classify_calls(calls=trace.tool_calls)
    return trace


def wall_seconds(*, trace: RunTrace) -> float:
    return (trace.ended_at - trace.started_at).total_seconds()


def with_history_cache(*, messages: list[dict]) -> list[dict]:
    """Mark the newest message as a cache breakpoint, so each turn re-reads the history from
    cache instead of paying full input price for it. Claude clients cache the same way; leaving
    it out would overstate the cost of every multi-turn run."""
    *history, last = messages
    content = last["content"]
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
    blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    return [*history, {**last, "content": blocks}]
