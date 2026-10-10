"""Default house rules for the AI review. Used when a template has no `ai:` lines."""

TECHNICAL = [
    "Use a formal, objective, third-person tone. No first person (I, we), slang, humour or contractions.",
    "Each finding states what was found, why it matters (impact) and how to fix it (recommendation).",
    "Severity names are consistent everywhere: Critical, High, Medium, Low, Informational. No other spellings.",
    "Client name, dates and the consultant's name are written the same way throughout the report.",
    "No draft wording or notes to self: TBD, TODO, XXX, lorem ipsum, unfilled [brackets] or {{tokens}}.",
    "Sentences are complete and clear; no run-on sentences, repeated words or unexplained abbreviations on first use.",
    "Headings, table labels and bullet lists use consistent capitalisation and style.",
]

EXECUTIVE = [
    "Plain language for a non-technical reader. Avoid unexplained jargon, command lines, ports and CVE identifiers.",
    "State the overall risk level and what it means for the business in the opening summary.",
    "Use a formal, objective, third-person tone. No first person (I, we), slang or contractions.",
    "Severity names are consistent everywhere: Critical, High, Medium, Low, Informational.",
    "Client name, dates and the consultant's name are written the same way throughout the report.",
    "No draft wording or notes to self: TBD, TODO, XXX, lorem ipsum, unfilled [brackets] or {{tokens}}.",
    "Recommendations are specific, actionable and prioritised.",
]


def defaults_for(report_type: str) -> list[str]:
    return list(TECHNICAL if report_type == "technical" else EXECUTIVE)
