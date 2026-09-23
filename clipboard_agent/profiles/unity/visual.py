from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol


class VisualIntent(str, Enum):
    GAME = "GAME"
    SCENE = "SCENE"
    EDITOR = "EDITOR"
    RUNTIME = "RUNTIME"


class VisualConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    intent: VisualIntent
    source: str
    confidence: VisualConfidence
    artifacts: tuple[Path, ...] = ()
    semantic_data: dict[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    fallback_used: bool = False
    occlusion_safe: bool = False
    frame_stable: bool = False

    @property
    def usable(self) -> bool:
        return self.confidence != VisualConfidence.INVALID and bool(self.artifacts or self.semantic_data)


class UnityVisualProvider(Protocol):
    provider_id: str
    priority: int

    def supports(self, intent: VisualIntent) -> bool: ...

    def capture(self, intent: VisualIntent, project_root: Path) -> EvidenceBundle: ...


class VisualCaptureError(RuntimeError):
    pass


class UnityVisualRouter:
    """Select the strongest available visual evidence without exposing backend plumbing to the LLM."""

    _CONFIDENCE_RANK = {
        VisualConfidence.INVALID: 0,
        VisualConfidence.LOW: 1,
        VisualConfidence.MEDIUM: 2,
        VisualConfidence.HIGH: 3,
    }

    def __init__(self, providers: tuple[UnityVisualProvider, ...] = ()) -> None:
        self.providers = tuple(sorted(providers, key=lambda item: item.priority, reverse=True))

    def capture(self, intent: VisualIntent, project_root: Path) -> EvidenceBundle:
        candidates = [provider for provider in self.providers if provider.supports(intent)]
        if not candidates:
            raise VisualCaptureError(f"Aucun provider visuel Unity pour l'intention {intent.value}.")

        best: EvidenceBundle | None = None
        failures: list[str] = []
        for index, provider in enumerate(candidates):
            try:
                evidence = provider.capture(intent, project_root)
            except Exception as exc:  # provider boundary: convert to deterministic fallback
                failures.append(f"{provider.provider_id}: {exc}")
                continue
            if evidence.intent != intent:
                failures.append(f"{provider.provider_id}: intention retournée incohérente")
                continue
            if index > 0 and evidence.usable and not evidence.fallback_used:
                evidence = EvidenceBundle(
                    intent=evidence.intent,
                    source=evidence.source,
                    confidence=evidence.confidence,
                    artifacts=evidence.artifacts,
                    semantic_data=evidence.semantic_data,
                    warnings=evidence.warnings,
                    fallback_used=True,
                    occlusion_safe=evidence.occlusion_safe,
                    frame_stable=evidence.frame_stable,
                )
            if best is None or self._CONFIDENCE_RANK[evidence.confidence] > self._CONFIDENCE_RANK[best.confidence]:
                best = evidence
            if evidence.usable and evidence.confidence == VisualConfidence.HIGH:
                return evidence

        if best is not None and best.usable:
            if failures:
                return EvidenceBundle(
                    intent=best.intent,
                    source=best.source,
                    confidence=best.confidence,
                    artifacts=best.artifacts,
                    semantic_data=best.semantic_data,
                    warnings=tuple((*best.warnings, *failures)),
                    fallback_used=best.fallback_used,
                    occlusion_safe=best.occlusion_safe,
                    frame_stable=best.frame_stable,
                )
            return best
        detail = "; ".join(failures) or "toutes les captures sont invalides"
        raise VisualCaptureError(f"Aucune preuve visuelle Unity exploitable : {detail}")
