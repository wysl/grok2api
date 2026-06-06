"""Control-plane upstream provider models."""

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.platform.runtime.clock import now_ms


class UpstreamType(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    SUB2API = "sub2api"
    NEW_API = "new_api"


class UpstreamStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DELETED = "deleted"


class UpstreamHealth(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


def new_provider_id() -> str:
    return str(uuid4())


class UpstreamProviderRecord(BaseModel):
    """Persistent upstream provider record.

    ``base_url`` may be either a service root or a ``/v1`` root. The forwarder
    normalises OpenAI-compatible paths at request time.
    """

    id: str = Field(default_factory=new_provider_id)
    name: str
    type: UpstreamType = UpstreamType.OPENAI_COMPATIBLE
    base_url: str
    api_key: str
    status: UpstreamStatus = UpstreamStatus.ENABLED
    models: list[str] = Field(default_factory=list)
    weight: int = Field(default=1, ge=1)
    timeout_sec: float = Field(default=120.0, gt=0)
    last_health: UpstreamHealth = UpstreamHealth.UNKNOWN
    last_health_at: int | None = None
    last_error: str | None = None
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)
    deleted_at: int | None = None
    ext: dict[str, Any] = Field(default_factory=dict)
    revision: int = 0

    @field_validator("id", "name", "api_key", mode="before")
    @classmethod
    def _strip_required(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("field cannot be empty")
        return text

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        text = str(value or "").strip().rstrip("/")
        if not text:
            raise ValueError("base_url cannot be empty")
        if not (text.startswith("http://") or text.startswith("https://")):
            raise ValueError("base_url must start with http:// or https://")
        return text

    @field_validator("models", mode="before")
    @classmethod
    def _normalize_models(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = value.split(",")
        else:
            raw_items = list(value)
        seen: list[str] = []
        for item in raw_items:
            model = str(item or "").strip()
            if model and model not in seen:
                seen.append(model)
        return seen

    def is_enabled(self) -> bool:
        return self.deleted_at is None and self.status == UpstreamStatus.ENABLED

    def is_deleted(self) -> bool:
        return self.deleted_at is not None or self.status == UpstreamStatus.DELETED


class UpstreamMutationResult(BaseModel):
    upserted: int = 0
    patched: int = 0
    deleted: int = 0
    revision: int = 0
    id: str | None = None


class UpstreamPage(BaseModel):
    items: list[UpstreamProviderRecord] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50
    total_pages: int = 1
    revision: int = 0


class UpstreamChangeSet(BaseModel):
    revision: int = 0
    items: list[UpstreamProviderRecord] = Field(default_factory=list)
    deleted_ids: list[str] = Field(default_factory=list)
    has_more: bool = False


class UpstreamRuntimeSnapshot(BaseModel):
    revision: int = 0
    items: list[UpstreamProviderRecord] = Field(default_factory=list)


__all__ = [
    "UpstreamProviderRecord",
    "UpstreamType",
    "UpstreamStatus",
    "UpstreamHealth",
    "UpstreamMutationResult",
    "UpstreamPage",
    "UpstreamChangeSet",
    "UpstreamRuntimeSnapshot",
    "new_provider_id",
]
