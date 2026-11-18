"""Cost of a run at list price, from pricing.json.

Each model entry: {"input": $, "output": $, "cache_write": $, "cache_read": $, "source": "...", "as_of": "YYYY-MM-DD"},
all per million tokens, plus an optional "long_context" block with the same four rates and an
"above_prompt_tokens" threshold. Input tokens here are the uncached part only; cache writes
and reads are billed at their own rates, as the API reports them separately.

Pricing is per request, not per run: a long-context tier applies to a request whose own
prompt crosses the threshold, and a long agent run usually has early requests below it and
later ones above it.
"""

import json
from pathlib import Path

PRICING_FILE = Path(__file__).parent / "pricing.json"
PER_MILLION = 1_000_000


def load_prices(*, model: str) -> dict:
    models = json.loads(PRICING_FILE.read_text())["models"]
    if model not in models:
        raise SystemExit(f"no list price for {model} in {PRICING_FILE.name}; add it before running")
    return models[model]


def request_cost(*, prices: dict, input_tokens: int, output_tokens: int, cache_write_tokens: int, cache_read_tokens: int) -> float:
    prompt_tokens = input_tokens + cache_write_tokens + cache_read_tokens
    long_context = prices.get("long_context")
    rates = long_context if long_context is not None and prompt_tokens > long_context["above_prompt_tokens"] else prices
    return (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_write_tokens * rates["cache_write"]
        + cache_read_tokens * rates["cache_read"]
    ) / PER_MILLION
