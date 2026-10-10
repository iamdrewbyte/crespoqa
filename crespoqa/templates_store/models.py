"""Template models. A template = service + report type + ordered QA steps with parameters."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class _Step(BaseModel):
    enabled: bool = True


class PeriodRemovalParams(BaseModel):
    labels: list[str] = ["Period:"]
    period_label_action: Literal["remove", "fill"] = "remove"


class PeriodRemovalStep(_Step):
    type: Literal["period_removal"] = "period_removal"
    params: PeriodRemovalParams = PeriodRemovalParams()


class PlaceholderMapping(BaseModel):
    token: str
    field: Literal["consultant", "reviewer", "client", "report_date", "period"]


class PlaceholdersParams(BaseModel):
    mappings: list[PlaceholderMapping] = []


class PlaceholdersStep(_Step):
    type: Literal["placeholders"] = "placeholders"
    params: PlaceholdersParams = PlaceholdersParams()


class FindReplaceRule(BaseModel):
    find: str
    replace: str
    scope: Literal["all", "page1"] = "all"


class FindReplaceParams(BaseModel):
    rules: list[FindReplaceRule] = []


class FindReplaceStep(_Step):
    type: Literal["find_replace"] = "find_replace"
    params: FindReplaceParams = FindReplaceParams()


class TocUpdateParams(BaseModel):
    # Asking Word to refresh fields on open makes it evaluate page references BEFORE pagination, so every page
    # number becomes 1. Off by default (proved on real reports). A new key, so old stored values are ignored.
    ask_word_to_refresh: bool = False


class TocUpdateStep(_Step):
    type: Literal["toc_update"] = "toc_update"
    params: TocUpdateParams = TocUpdateParams()


class DocumentDetailsParams(BaseModel):
    matcher_text: list[str] = ["Document Details"]
    fallback_page: int | None = 4
    # property name -> form field. Applied BEFORE fields are updated.
    property_mappings: dict[str, Literal["consultant", "reviewer", "client", "report_date", "period"]] = {
        "author": "consultant"
    }
    # Optional: put a form field into a specific cell, e.g. {"row_label": "Author", "field": "consultant"}
    cell_mappings: list[dict[str, str]] = []


class DocumentDetailsStep(_Step):
    type: Literal["document_details"] = "document_details"
    params: DocumentDetailsParams = DocumentDetailsParams()


class TableFixParams(BaseModel):
    cant_split: bool = True
    header_repeat: Literal["off", "multi_row"] = "multi_row"
    header_min_rows: int = 2
    remove_empty_page_breaks_in_cells: bool = True
    keep_together_max_rows: int = 15  # tables up to this many rows move whole to the next page instead of splitting; 0 = off


class TableFixStep(_Step):
    type: Literal["table_fix"] = "table_fix"
    params: TableFixParams = TableFixParams()


class AiRulesParams(BaseModel):
    rules: list[str] = []


class AiRulesStep(_Step):
    """House rules the optional AI review checks the report against. Not run by the QA pipeline."""
    type: Literal["ai_rules"] = "ai_rules"
    params: AiRulesParams = AiRulesParams()


class FinalRefreshStep(_Step):
    type: Literal["final_refresh"] = "final_refresh"
    params: dict[str, Any] = {}


Step = Annotated[
    Union[
        PeriodRemovalStep, PlaceholdersStep, FindReplaceStep, TocUpdateStep,
        DocumentDetailsStep, TableFixStep, FinalRefreshStep, AiRulesStep,
    ],
    Field(discriminator="type"),
]


class Template(BaseModel):
    name: str
    service: str
    report_type: Literal["technical", "executive"]
    version: int = 1
    steps: list[Step]
