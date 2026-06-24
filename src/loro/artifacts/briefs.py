from dataclasses import dataclass


@dataclass(frozen=True)
class Brief:
    title: str
    summary: str
    risks: list[str]
    next_steps: list[str]
