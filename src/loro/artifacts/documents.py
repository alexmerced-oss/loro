from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentDraft:
    title: str
    markdown: str


def create_markdown_document(title: str, body: str) -> DocumentDraft:
    return DocumentDraft(title=title, markdown=f"# {title}\n\n{body.strip()}\n")
