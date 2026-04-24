"""Cost tracker — translation of cost-tracker.ts."""

from __future__ import annotations

# Approximate token costs (USD per 1M tokens) for common models
MODEL_COSTS: dict[str, tuple[float, float]] = {
    # (input_cost_per_1M, output_cost_per_1M)
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "o3-mini": (1.10, 4.40),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-6": (15.00, 75.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for the given token usage."""
    costs = MODEL_COSTS.get(model)
    if costs is None:
        # Default to GPT-4o pricing
        costs = MODEL_COSTS["gpt-4o"]

    input_cost = (input_tokens / 1_000_000) * costs[0]
    output_cost = (output_tokens / 1_000_000) * costs[1]
    return input_cost + output_cost
