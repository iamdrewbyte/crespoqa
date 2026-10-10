"""Change report. Holds only tool-authored labels and counts, never report content."""
from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass, field


@dataclass
class TaskResult:
    key: str
    title: str
    status: str = "ok"  # ok | warning | skipped | failed
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class DocReport:
    job_id: str
    input_name: str
    report_type: str
    template: str
    template_version: int
    processed_by: str
    dry_run: bool
    status: str = "success"  # success | success_with_warnings | failed
    error: str | None = None
    output_name: str | None = None
    duration_s: float = 0.0
    tasks: list[TaskResult] = field(default_factory=list)

    def finalize(self) -> None:
        if self.error:
            self.status = "failed"
        elif any(t.warnings or t.status in ("warning", "failed") for t in self.tasks):
            self.status = "success_with_warnings"
        else:
            self.status = "success"

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def to_html(self) -> str:
        e = html.escape
        rows = []
        for t in self.tasks:
            items = "".join(f"<li>{e(c)}</li>" for c in t.changes)
            items += "".join(f'<li class="warn">Warning: {e(x)}</li>' for x in t.warnings)
            rows.append(f"<section><h3>{e(t.title)} <small>[{e(t.status)}]</small></h3><ul>{items or '<li>No changes.</li>'}</ul></section>")
        mode = " (dry run, no file produced)" if self.dry_run else ""
        err = f'<p class="warn">Failed: {e(self.error)}</p>' if self.error else ""
        return (
            "<!doctype html><meta charset='utf-8'><title>CrespoQA Change Report</title>"
            "<style>body{background:#0f1218;color:#e6e9ef;font:15px system-ui;max-width:760px;margin:2rem auto;padding:0 1rem}"
            "h1,h3{margin:.4em 0}small{color:#8b93a5}.warn{color:#f5b84a}li{margin:.25em 0}</style>"
            f"<h1>CrespoQA Change Report{e(mode)}</h1>"
            f"<p>File: {e(self.input_name)} &middot; Template: {e(self.template)} v{self.template_version} "
            f"&middot; Job {e(self.job_id)}<br>Processed by: {e(self.processed_by or 'n/a')} &middot; "
            f"Status: <b>{e(self.status)}</b> &middot; {self.duration_s:.1f}s</p>{err}{''.join(rows)}"
        )
