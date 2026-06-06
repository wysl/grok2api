"""Upstream provider lease."""

from dataclasses import dataclass

from app.control.upstream.models import UpstreamProviderRecord
from app.platform.runtime.ids import next_id


@dataclass(slots=True)
class UpstreamLease:
    lease_id: int
    provider: UpstreamProviderRecord
    selected_at: int


def new_lease(provider: UpstreamProviderRecord, selected_at: int) -> UpstreamLease:
    return UpstreamLease(
        lease_id=next_id(),
        provider=provider,
        selected_at=selected_at,
    )


__all__ = ["UpstreamLease", "new_lease"]
