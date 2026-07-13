from __future__ import annotations

from decimal import Decimal

from agent_harness.tracing.usage import PricingSnapshot


PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing"


def pricing_snapshot(provider: str, model: str) -> PricingSnapshot | None:
    """Return the audited provider/model price effective when this build was made."""
    if provider != "deepseek":
        return None
    rows = {
        "deepseek-v4-flash": ("0.02", "1", "2"),
        "deepseek-v4-pro": ("0.025", "3", "6"),
    }
    prices = rows.get(model)
    if prices is None:
        return None
    hit, miss, output = prices
    return PricingSnapshot(snapshot_id=f"{model}-cny-2026-07-13", provider=provider, model=model, currency="CNY",
        unit_tokens=1_000_000, cache_hit_input_per_unit=Decimal(hit), cache_miss_input_per_unit=Decimal(miss),
        output_per_unit=Decimal(output), effective_from="2026-07-13", source_url=PRICE_SOURCE)
