from dataclasses import dataclass


@dataclass(frozen=True)
class Slide:
    title: str
    bullets: list[str]


@dataclass(frozen=True)
class PresentationOutline:
    title: str
    slides: list[Slide]
