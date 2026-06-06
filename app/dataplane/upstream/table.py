"""In-memory upstream provider table."""

from dataclasses import dataclass, field

from app.control.upstream.models import UpstreamProviderRecord


@dataclass
class UpstreamRuntimeTable:
    providers: dict[str, UpstreamProviderRecord] = field(default_factory=dict)
    model_index: dict[str, set[str]] = field(default_factory=dict)
    inflight: dict[str, int] = field(default_factory=dict)
    rr_cursor: int = 0
    revision: int = 0

    @property
    def size(self) -> int:
        return len(self.providers)

    def upsert(self, record: UpstreamProviderRecord) -> None:
        self.delete(record.id)
        self.providers[record.id] = record
        self.inflight.setdefault(record.id, 0)
        if record.is_enabled():
            for model in record.models:
                self.model_index.setdefault(model, set()).add(record.id)

    def delete(self, provider_id: str) -> None:
        existing = self.providers.pop(provider_id, None)
        self.inflight.pop(provider_id, None)
        if existing is None:
            return
        for model in existing.models:
            providers = self.model_index.get(model)
            if providers:
                providers.discard(provider_id)
                if not providers:
                    self.model_index.pop(model, None)

    def models(self) -> list[str]:
        return sorted(self.model_index)

    def providers_for_model(self, model: str) -> list[UpstreamProviderRecord]:
        ids = self.model_index.get(model) or set()
        return [
            self.providers[provider_id]
            for provider_id in ids
            if provider_id in self.providers
            and self.providers[provider_id].is_enabled()
        ]


def make_empty_table() -> UpstreamRuntimeTable:
    return UpstreamRuntimeTable()


__all__ = ["UpstreamRuntimeTable", "make_empty_table"]
