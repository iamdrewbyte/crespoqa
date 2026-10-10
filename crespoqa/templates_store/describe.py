"""Plain-English description of what a template will run."""
from __future__ import annotations

from . import models as m


def _q(items: list[str]) -> str:
    return ", ".join(f"'{i}'" for i in items) or "(none)"


def describe_step(s) -> str:
    p = s.params
    if isinstance(s, m.PeriodRemovalStep):
        if p.period_label_action == "fill":
            return (f"On page 1 (body, first-page header, text boxes), replace the label {_q(p.labels)} with the "
                    "engagement period entered on the form. Surrounding formatting is kept.")
        return (f"On page 1 (body, first-page header, text boxes), delete the label {_q(p.labels)} and the single space "
                "after it. Any value after the label (such as a date) is kept. Other pages are not touched.")
    if isinstance(s, m.PlaceholdersStep):
        if not p.mappings:
            return "Fill placeholders: none configured."
        return "Replace placeholders everywhere (body, headers, footers, tables, text boxes, notes): " + \
            "; ".join(f"'{x.token}' becomes the {x.field.replace('_', ' ')}" for x in p.mappings) + "."
    if isinstance(s, m.FindReplaceStep):
        if not p.rules:
            return "Custom find/replace: no rules configured."
        return "Custom find/replace: " + "; ".join(
            f"'{r.find}' becomes '{r.replace}' ({'page 1 only' if r.scope == 'page1' else 'whole document'})" for r in p.rules) + "."
    if isinstance(s, m.TocUpdateStep):
        base = ("Set the page numbers of the contents (including a 'Page Number' table) from a rendered layout of the finished "
                "document, and turn them into page-reference fields.")
        return base + (" Word is also asked to refresh them on open (warning: this can turn every number into 1)."
                       if p.ask_word_to_refresh else
                       " Word is not asked to refresh them, so no prompt appears; select the contents and press F9 for an exact refresh.")
    if isinstance(s, m.DocumentDetailsStep):
        parts = [f"Find the Document Details table: a top-level table titled {_q(p.matcher_text)}"
                 + (f", otherwise the table on page {p.fallback_page}" if p.fallback_page else "")
                 + "; a box nested inside another table is used only as a last resort."]
        if p.property_mappings:
            parts.append("Before updating fields, set document properties: " +
                         ", ".join(f"{k} from the {v.replace('_', ' ')}" for k, v in p.property_mappings.items()) + ".")
        for c in p.cell_mappings:
            parts.append(f"Fill the row labelled '{c.get('row_label', '')}' from the {c.get('field', '').replace('_', ' ')}.")
        parts.append("Update the fields in the table, remove fixed row heights, switch to auto-fit and remove text compression so nothing clips.")
        return " ".join(parts)
    if isinstance(s, m.TableFixStep):
        bits = []
        if p.cant_split:
            bits.append("stop every table row (including nested tables) from splitting across pages")
        if p.header_repeat != "off":
            bits.append(f"repeat the first row as a header on tables with {p.header_min_rows}+ rows")
        if p.remove_empty_page_breaks_in_cells:
            bits.append("remove empty manual page breaks inside table cells (page breaks outside tables are kept)")
        if p.keep_together_max_rows:
            bits.append(f"keep tables of up to {p.keep_together_max_rows} rows together: if one does not fit at the bottom of a page, "
                        "the whole table (with the sentence introducing it) moves to the next page")
        return "For all tables: " + "; ".join(bits) + "." if bits else "Table fixes: nothing enabled."
    if isinstance(s, m.AiRulesStep):
        return (f"AI review rules ({len(p.rules)}): the optional AI review checks the report against these and flags "
                "possible problems for a person to accept or ignore. It never changes the document. Rules: " +
                " / ".join(p.rules[:8]) + (" ..." if len(p.rules) > 8 else ""))
    if isinstance(s, m.FinalRefreshStep):
        return ("Re-evaluate title, author, filename and document-property fields in the body, headers and footers; "
                "mark page numbers, references and indexes to refresh when the file opens in Word.")
    return s.type


def describe(t: m.Template) -> list[dict]:
    return [{"n": i, "type": s.type, "enabled": s.enabled,
             "text": describe_step(s) if s.enabled else "(disabled) " + describe_step(s)}
            for i, s in enumerate(t.steps, 1)]
