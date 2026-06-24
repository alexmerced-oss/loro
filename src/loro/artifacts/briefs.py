from dataclasses import dataclass
from pathlib import Path

from loro.artifacts.common import ArtifactResult, ensure_output_dir, slugify, title_from_prompt


@dataclass(frozen=True)
class Brief:
    title: str
    summary: str
    risks: list[str]
    next_steps: list[str]


def create_brief_artifact(
    prompt: str,
    output_dir: Path,
    brief_type: str = "meeting",
) -> ArtifactResult:
    title = title_from_prompt(prompt, f"Loro {brief_type.title()} Brief")
    slug = slugify(f"{brief_type}-{title}", "brief")
    output_dir = ensure_output_dir(output_dir)
    path = output_dir / f"{slug}.md"
    brief = Brief(
        title=title,
        summary=prompt.strip(),
        risks=[
            "Source context may be incomplete.",
            "Facts and metrics require human validation before distribution.",
        ],
        next_steps=[
            "Confirm audience and decision needed.",
            "Attach source documents or governed data references.",
            "Assign owners for follow-up actions.",
        ],
    )
    path.write_text(
        f"# {brief.title}\n\n"
        f"Type: {brief_type}\n\n"
        "## Summary\n\n"
        f"{brief.summary}\n\n"
        "## Risks\n\n"
        + "\n".join(f"- {risk}" for risk in brief.risks)
        + "\n\n## Next Steps\n\n"
        + "\n".join(f"- {step}" for step in brief.next_steps)
        + "\n",
        encoding="utf-8",
    )
    return ArtifactResult(
        title=title,
        kind=f"{brief_type}-brief",
        paths=[path],
        summary=f"Created {brief_type} brief artifact: {path}",
    )
