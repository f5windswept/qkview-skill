from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Finding:
    severity: str
    title: str
    detail: str
    source: str
    impact: str = ""


@dataclass
class MetricNote:
    name: str
    value: str
    assessment: str


@dataclass
class QKViewBundle:
    qkview_id: str
    summary: Dict[str, Any] = field(default_factory=dict)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisReport:
    scope: List[str]
    executive_assessment: List[str]
    critical_findings: List[str]
    warnings: List[str]
    performance_notes: List[str]
    configuration_notes: List[str]
    recommended_next_actions: List[str]


def optional_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
