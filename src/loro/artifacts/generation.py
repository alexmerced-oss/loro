import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from loro.artifacts.briefs import Brief
from loro.artifacts.documents import DocumentDraft
from loro.artifacts.presentations import PresentationOutline, Slide


class DocumentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["document"]
    title: str = Field(min_length=1, max_length=160)
    body_markdown: str = Field(min_length=1)


class SlidePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    bullets: list[str] = Field(min_length=1, max_length=12)


class PresentationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["presentation"]
    title: str = Field(min_length=1, max_length=160)
    slides: list[SlidePayload] = Field(min_length=1, max_length=30)


class SpreadsheetPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["spreadsheet"]
    title: str = Field(min_length=1, max_length=160)
    columns: list[str] = Field(min_length=1, max_length=30)
    rows: list[list[str | int | float | bool | None]] = Field(min_length=1, max_length=5000)


class BriefPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["brief"]
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1)
    risks: list[str] = Field(min_length=1, max_length=30)
    next_steps: list[str] = Field(min_length=1, max_length=30)


ArtifactPayload = Annotated[
    DocumentPayload | PresentationPayload | SpreadsheetPayload | BriefPayload,
    Field(discriminator="kind"),
]
_PAYLOAD_ADAPTER = TypeAdapter(ArtifactPayload)


def generation_prompt(kind: str, prompt: str, *, brief_type: str | None = None) -> str:
    fields = {
        "document": "kind, title, body_markdown",
        "presentation": "kind, title, slides; every slide has title and bullets",
        "spreadsheet": "kind, title, columns, rows; every row matches the columns",
        "brief": "kind, title, summary, risks, next_steps",
    }[kind]
    brief_note = f" The brief type is {brief_type}." if brief_type else ""
    return (
        f"Create a complete {kind} from the user's request below.{brief_note} "
        "Use substantive, accurate content rather than instructions about what to write. "
        "Do not call tools. Return exactly one JSON object and no markdown fence. "
        f"The object fields are: {fields}. Set kind to {kind!r}. "
        "All prose and table values must be safe to present directly to the user.\n\n"
        f"USER REQUEST:\n{prompt.strip()}"
    )


def parse_generated_payload(content: str, *, expected_kind: str) -> ArtifactPayload:
    decoder = json.JSONDecoder()
    payload: object | None = None
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        payload = candidate
        break
    if payload is None:
        raise ValueError("The model did not return a JSON artifact draft.")
    try:
        draft = _PAYLOAD_ADAPTER.validate_python(payload)
    except ValidationError as error:
        raise ValueError(f"The model returned an invalid artifact draft: {error}") from error
    if draft.kind != expected_kind:
        raise ValueError(
            f"The model returned artifact kind {draft.kind!r}; expected {expected_kind!r}."
        )
    if isinstance(draft, SpreadsheetPayload):
        mismatched = [
            index for index, row in enumerate(draft.rows) if len(row) != len(draft.columns)
        ]
        if mismatched:
            raise ValueError(
                "The model returned spreadsheet rows that do not match the column count: "
                + ", ".join(str(index) for index in mismatched[:10])
            )
    return draft


def document_draft(payload: DocumentPayload) -> DocumentDraft:
    return DocumentDraft(title=payload.title, markdown=payload.body_markdown.strip())


def presentation_draft(payload: PresentationPayload) -> PresentationOutline:
    return PresentationOutline(
        title=payload.title,
        slides=[Slide(title=item.title, bullets=item.bullets) for item in payload.slides],
    )


def brief_draft(payload: BriefPayload) -> Brief:
    return Brief(
        title=payload.title,
        summary=payload.summary,
        risks=payload.risks,
        next_steps=payload.next_steps,
    )
