from __future__ import annotations

from dataclasses import dataclass, field

CHECKS = ("placeholders", "details_table", "consistency", "counts", "typos", "style")
SEVERITIES = ("info", "warning")


@dataclass
class Finding:
    id: str
    check: str
    severity: str
    doc: str            # technical | executive | both
    message: str
    evidence: str = ""  # short verbatim excerpt (<=160 chars) so a human can locate the issue
    suggestion: str = ""
    decision: str = "open"  # open | accepted | ignored  (never changes the document)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ReviewState:
    status: str = "pending"  # pending | running | done | error
    progress: str = ""
    error: str | None = None
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"status": self.status, "progress": self.progress, "error": self.error, "notes": self.notes,
                "findings": [f.to_dict() for f in self.findings]}
