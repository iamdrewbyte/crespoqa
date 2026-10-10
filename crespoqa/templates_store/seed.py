"""Default templates. PCI is intentionally absent: it is created through the Templates UI (M3)."""
from __future__ import annotations

from .ai_defaults import defaults_for
from .models import (
    AiRulesParams, AiRulesStep, DocumentDetailsStep, FinalRefreshStep, PeriodRemovalStep, PlaceholderMapping,
    PlaceholdersParams, PlaceholdersStep, TableFixStep, Template, TocUpdateStep,
)

_TOKENS = [
    PlaceholderMapping(token="[Consultant Name]", field="consultant"),
    PlaceholderMapping(token="{{CONSULTANT}}", field="consultant"),
]


def _steps(report_type: str = "technical"):
    return [
        PeriodRemovalStep(),
        PlaceholdersStep(params=PlaceholdersParams(mappings=list(_TOKENS))),
        TocUpdateStep(),
        DocumentDetailsStep(),
        TableFixStep(),
        FinalRefreshStep(),
        AiRulesStep(params=AiRulesParams(rules=defaults_for(report_type))),
    ]


def seed_templates() -> list[Template]:
    return [
        Template(name=f"{service} - {rtype.title()}", service=service, report_type=rtype, steps=_steps(rtype))
        for service in ("WebApp", "VA")
        for rtype in ("technical", "executive")
    ]


def get_template(service: str, report_type: str) -> Template:
    for t in seed_templates():
        if t.service.lower() == service.lower() and t.report_type == report_type:
            return t
    raise KeyError(f"No template for service={service!r} type={report_type!r}")
