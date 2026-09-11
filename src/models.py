"""Versioned result contract; browser dependencies are imported lazily."""
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ScrapeResult:
    url: str
    engine_used: str
    markdown: str
    success: bool
    error: str | None = None
    elapsed_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    final_url: str | None = None
    status_code: int | None = None
    title: str | None = None
    truncated: bool = False
    error_code: str | None = None
    retryable: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["engine"] = result.pop("engine_used")
        result["schema_version"] = "1.1"
        result["content_is_untrusted"] = True
        result["elapsed_ms"] = round(self.elapsed_ms, 2)
        return result


def failure(url: str, engine: str, code: str, message: str, retryable=False) -> ScrapeResult:
    return ScrapeResult(url, engine, "", False, error=message, error_code=code, retryable=retryable)
