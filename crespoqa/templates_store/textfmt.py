"""Plain-text ("Notepad") form of a template: one step per line, converted to and from the step models."""
from __future__ import annotations

import re

from . import models as m

FIELDS = {"consultant": "consultant", "reviewer": "reviewer", "client": "client",
          "date": "report_date", "report_date": "report_date", "period": "period"}
FIELD_OUT = {"consultant": "consultant", "reviewer": "reviewer", "client": "client", "report_date": "date", "period": "period"}

HEADER = (
    "# CrespoQA template. One step per line, run from top to bottom.\n"
    "# Put the word  off  at the start of a line to switch that step off. Lines starting with # are notes.\n"
)

HELP = """Steps you can write (one per line):

  period: remove "Period:"                      delete the label on page 1  (use  fill  to put the engagement period instead)
  placeholders: "[Consultant Name]" = consultant ; "{{CLIENT}}" = client      (consultant, reviewer, client, date, period)
  replace: "old text" -> "new text" ; "other" -> "x" @page1                    (@page1 = page 1 only)
  toc                                           set the contents page numbers (no Word prompt)
  toc: refresh                                  also ask Word to refresh on open - not recommended: Word then
                                                evaluates page references before pagination and shows 1 everywhere
  details: title "Document Details" ; page 4 ; set author = consultant ; row "Author" = consultant
  tables: cantsplit on ; header rows 2 ; empty-page-breaks remove ; keep-together 15
  refresh                                       final refresh of title/author/filename and page numbers
  ai: Use a formal, third-person tone.          one house rule per line; the optional AI review checks the report
                                                against these rules (it only flags, it never edits)

Start a line with  off  to disable it, for example:   off replace: "foo" -> "bar"
"""


class TextError(ValueError):
    """Message is shown to the user, so it names the line."""


_Q = r'"((?:[^"\\]|\\.)*)"'


def _unq(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _split(s: str, sep: str = ";") -> list[str]:
    out, cur, inq, esc = [], "", False, False
    for ch in s:
        if inq:
            cur += ch
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                inq = False
        elif ch == '"':
            inq = True; cur += ch
        elif ch == sep:
            out.append(cur.strip()); cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return [p for p in out if p]


def _quoted(s: str) -> list[str]:
    return [_unq(x) for x in re.findall(_Q, s)]


def _field(name: str, n: int) -> str:
    key = name.strip().lower()
    if key not in FIELDS:
        raise TextError(f"Line {n}: '{name}' is not a form field. Use: consultant, reviewer, client, date, period.")
    return FIELDS[key]


def from_text(text: str) -> list:
    steps = []
    rule_lines: list[tuple[bool, str]] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        enabled = True
        if re.match(r"^off(\s|:|$)", line, re.I):
            enabled = False
            line = line[3:].lstrip(" :")
            if not line:
                raise TextError(f"Line {n}: 'off' must be followed by a step.")
        mt = re.match(r"^([A-Za-z\-]+)\s*:?\s*(.*)$", line)
        kw, rest = mt.group(1).lower(), mt.group(2).strip()
        if kw == "ai":
            if not rest:
                raise TextError(f"Line {n}: write the rule after 'ai:'")
            rule_lines.append((enabled, rest))
            continue
        try:
            steps.append(_parse_step(kw, rest, n, enabled))
        except TextError:
            raise
        except Exception as exc:  # pydantic validation etc.
            raise TextError(f"Line {n}: {exc}") from None
    rules = [x for x in rule_lines if x[1]]
    if rules:
        steps.append(m.AiRulesStep(enabled=all(e for e, _ in rules), params=m.AiRulesParams(rules=[r for _, r in rules])))
    if not steps:
        raise TextError("The template has no steps.")
    return steps


def _parse_step(kw: str, rest: str, n: int, enabled: bool):
    if kw == "period":
        first = rest.split(None, 1)[0].lower() if rest else ""
        if first not in ("remove", "fill"):
            raise TextError(f'Line {n}: write  period: remove "Period:"  or  period: fill "Period:"')
        labels = _quoted(rest) or ["Period:"]
        return m.PeriodRemovalStep(enabled=enabled, params=m.PeriodRemovalParams(labels=labels, period_label_action=first))
    if kw == "placeholders":
        maps = []
        for part in _split(rest):
            mt = re.match(rf"^{_Q}\s*=\s*(\w+)$", part)
            if not mt:
                raise TextError(f'Line {n}: could not read  {part[:40]}  - write it like  "[Consultant Name]" = consultant')
            maps.append(m.PlaceholderMapping(token=_unq(mt.group(1)), field=_field(mt.group(2), n)))
        return m.PlaceholdersStep(enabled=enabled, params=m.PlaceholdersParams(mappings=maps))
    if kw in ("replace", "find"):
        rules = []
        for part in _split(rest):
            mt = re.match(rf'^{_Q}\s*->\s*{_Q}\s*(@page1)?$', part)
            if not mt:
                raise TextError(f'Line {n}: could not read  {part[:40]}  - write it like  "old" -> "new"')
            rules.append(m.FindReplaceRule(find=_unq(mt.group(1)), replace=_unq(mt.group(2)), scope="page1" if mt.group(3) else "all"))
        return m.FindReplaceStep(enabled=enabled, params=m.FindReplaceParams(rules=rules))
    if kw in ("toc", "contents"):
        low = rest.lower().strip()
        if low not in ("", "refresh", "no-prompt"):
            raise TextError(f"Line {n}: after toc write nothing or 'refresh' (ask Word to refresh on open; can reset numbers to 1).")
        return m.TocUpdateStep(enabled=enabled, params=m.TocUpdateParams(ask_word_to_refresh=low == "refresh"))
    if kw in ("refresh", "final"):
        return m.FinalRefreshStep(enabled=enabled)
    if kw == "details":
        p = m.DocumentDetailsParams(property_mappings={}, cell_mappings=[])
        for part in _split(rest):
            low = part.lower()
            if low.startswith("title"):
                p.matcher_text = _quoted(part) or [part[5:].strip()]
            elif low.startswith("page"):
                v = part[4:].strip().lower()
                p.fallback_page = None if v in ("none", "off", "") else int(v)
            elif low.startswith("set"):
                mt = re.match(r"^set\s+(\w+)\s*=\s*(\w+)$", part, re.I)
                if not mt:
                    raise TextError(f"Line {n}: write  set author = consultant")
                p.property_mappings[mt.group(1).lower()] = _field(mt.group(2), n)
            elif low.startswith("row"):
                mt = re.match(rf"^row\s+{_Q}\s*=\s*(\w+)$", part, re.I)
                if not mt:
                    raise TextError(f'Line {n}: write  row "Author" = consultant')
                p.cell_mappings.append({"row_label": _unq(mt.group(1)), "field": _field(mt.group(2), n)})
            else:
                raise TextError(f"Line {n}: unknown part '{part[:30]}'. Parts: title, page, set, row.")
        return m.DocumentDetailsStep(enabled=enabled, params=p)
    if kw == "tables":
        p = m.TableFixParams()
        for part in _split(rest):
            low = part.lower().split()
            if low[:1] == ["cantsplit"] and len(low) == 2 and low[1] in ("on", "off"):
                p.cant_split = low[1] == "on"
            elif low[:1] == ["header"] and low[1:2] == ["off"]:
                p.header_repeat = "off"
            elif low[:2] == ["header", "rows"] and len(low) == 3 and low[2].isdigit():
                p.header_repeat, p.header_min_rows = "multi_row", int(low[2])
            elif low[:1] == ["empty-page-breaks"] and len(low) == 2 and low[1] in ("remove", "keep"):
                p.remove_empty_page_breaks_in_cells = low[1] == "remove"
            elif low[:1] == ["keep-together"] and len(low) == 2 and (low[1] == "off" or low[1].isdigit()):
                p.keep_together_max_rows = 0 if low[1] == "off" else int(low[1])
            else:
                raise TextError(f"Line {n}: could not read '{part[:30]}'. Parts: cantsplit on|off ; header off | header rows N ; empty-page-breaks remove|keep ; keep-together N|off")
        return m.TableFixStep(enabled=enabled, params=p)
    raise TextError(f"Line {n}: unknown step '{kw}'. Steps: period, placeholders, replace, toc, details, tables, refresh.")


def to_text(steps: list) -> str:
    lines = [HEADER]
    for s in steps:
        p = getattr(s, "params", None)
        if isinstance(s, m.PeriodRemovalStep):
            body = f"period: {p.period_label_action} " + " ".join(_q(l) for l in p.labels)
        elif isinstance(s, m.PlaceholdersStep):
            body = "placeholders: " + " ; ".join(f"{_q(x.token)} = {FIELD_OUT[x.field]}" for x in p.mappings)
        elif isinstance(s, m.FindReplaceStep):
            body = "replace: " + " ; ".join(f"{_q(r.find)} -> {_q(r.replace)}" + (" @page1" if r.scope == "page1" else "") for r in p.rules)
        elif isinstance(s, m.TocUpdateStep):
            body = "toc: refresh" if p.ask_word_to_refresh else "toc"
        elif isinstance(s, m.FinalRefreshStep):
            body = "refresh"
        elif isinstance(s, m.DocumentDetailsStep):
            parts = ["title " + " ".join(_q(t) for t in p.matcher_text), f"page {p.fallback_page if p.fallback_page else 'none'}"]
            parts += [f"set {k} = {FIELD_OUT[v]}" for k, v in p.property_mappings.items()]
            parts += [f"row {_q(c['row_label'])} = {FIELD_OUT[c['field']]}" for c in p.cell_mappings]
            body = "details: " + " ; ".join(parts)
        elif isinstance(s, m.TableFixStep):
            parts = [f"cantsplit {'on' if p.cant_split else 'off'}",
                     "header off" if p.header_repeat == "off" else f"header rows {p.header_min_rows}",
                     f"empty-page-breaks {'remove' if p.remove_empty_page_breaks_in_cells else 'keep'}",
                     f"keep-together {p.keep_together_max_rows if p.keep_together_max_rows else 'off'}"]
            body = "tables: " + " ; ".join(parts)
        elif isinstance(s, m.AiRulesStep):
            for r in p.rules:
                lines.append(("off " if not s.enabled else "") + "ai: " + r.replace("\n", " "))
            continue
        else:
            continue
        lines.append(("off " if not s.enabled else "") + body)
    return "\n".join(lines) + "\n"
