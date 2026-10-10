"""Run all review checks and collect findings. Never touches any document."""
from __future__ import annotations

from typing import Callable

from ..core.context import RunForm
from .checks import (check_details_table, check_placeholders, check_rules, check_typos, compare_facts,
                     extract_counts, extract_facts)
from .extract import ExtractedDoc
from .llm import LLM, ReviewError
from .models import Finding, ReviewState


def run_review(docs: dict[str, ExtractedDoc], form: RunForm, llm: LLM | None, state: ReviewState, *,
               chunk_chars: int = 12000, max_chunks: int = 30, progress: Callable[[str], None] | None = None) -> ReviewState:
    """`llm=None` runs only the deterministic checks."""
    raw: list[dict] = []

    def step(label: str) -> None:
        state.progress = label
        if progress:
            progress(label)

    step("Scanning for leftover placeholders")
    raw += check_placeholders(docs)

    if llm is not None:
        chunked = {}
        for rtype, doc in docs.items():
            ch = doc.chunks(chunk_chars)
            if len(ch) > max_chunks:
                state.notes.append(f"The {rtype} report is long: only the first {max_chunks} sections were reviewed by the model.")
                ch = ch[:max_chunks]
            chunked[rtype] = ch or [""]
        facts, counts = {}, {}
        for rtype, doc in docs.items():
            step(f"Checking the Document Details table ({rtype})")
            raw += check_details_table(llm, doc, state.notes)
            step(f"Reading cover details ({rtype})")
            facts[rtype] = extract_facts(llm, doc, chunked[rtype], state.notes)
            step(f"Reading stated finding counts ({rtype})")
            counts[rtype] = extract_counts(llm, doc, chunked[rtype], state.notes)
        raw += compare_facts(facts, counts, form.consultant.strip())
        for rtype, doc in docs.items():
            step(f"Looking for typos ({rtype})")
            raw += check_typos(llm, doc, chunked[rtype], state.notes)
        for rtype, doc in docs.items():
            if doc.rules:
                step(f"Checking the house rules ({rtype})")
                raw += check_rules(llm, doc, chunked[rtype], state.notes)

    state.findings = [Finding(id=f"f{i}", **r) for i, r in enumerate(raw, 1)]
    return state
