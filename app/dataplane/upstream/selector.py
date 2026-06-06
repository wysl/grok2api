"""Upstream provider selection helpers."""

from collections.abc import Sequence

from app.control.upstream.models import UpstreamProviderRecord
from app.platform.config.snapshot import get_config


def current_strategy() -> str:
    raw = str(get_config("upstream.strategy", "local_first") or "local_first")
    strategy = raw.strip().lower()
    if strategy not in {"local_first", "upstream_first", "upstream_only", "weighted"}:
        return "local_first"
    return strategy


def select_provider(
    providers: Sequence[UpstreamProviderRecord],
    inflight: dict[str, int],
    *,
    cursor: int,
) -> tuple[UpstreamProviderRecord | None, int]:
    if not providers:
        return None, cursor
    expanded: list[UpstreamProviderRecord] = []
    for provider in sorted(providers, key=lambda item: item.id):
        expanded.extend([provider] * max(1, int(provider.weight)))
    if not expanded:
        return None, cursor

    # Weighted round-robin with an inflight tie-break for the selected window.
    start = cursor % len(expanded)
    window = expanded[start:] + expanded[:start]
    min_inflight = min(inflight.get(provider.id, 0) for provider in window)
    for offset, provider in enumerate(window):
        if inflight.get(provider.id, 0) == min_inflight:
            return provider, (start + offset + 1) % len(expanded)
    return window[0], (start + 1) % len(expanded)


__all__ = ["current_strategy", "select_provider"]
