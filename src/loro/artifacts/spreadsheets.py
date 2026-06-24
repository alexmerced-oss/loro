from dataclasses import dataclass


@dataclass(frozen=True)
class WorkbookPlan:
    title: str
    sheets: list[str]
    assumptions: list[str]
