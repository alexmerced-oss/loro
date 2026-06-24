from pathlib import Path

from openpyxl import load_workbook

from loro.artifacts.briefs import create_brief_artifact
from loro.artifacts.documents import create_document_artifact
from loro.artifacts.presentations import create_presentation_artifact
from loro.artifacts.spreadsheets import create_spreadsheet_artifact


def test_document_artifact(tmp_path: Path) -> None:
    result = create_document_artifact("Draft onboarding guide", tmp_path)
    assert len(result.paths) == 2
    assert result.paths[0].suffix == ".md"
    assert result.paths[1].suffix == ".docx"
    assert all(path.exists() for path in result.paths)


def test_presentation_artifact(tmp_path: Path) -> None:
    result = create_presentation_artifact("Quarterly business review", tmp_path)
    assert any(path.suffix == ".pptx" for path in result.paths)
    assert all(path.exists() for path in result.paths)


def test_spreadsheet_artifact(tmp_path: Path) -> None:
    result = create_spreadsheet_artifact("Launch readiness tracker", tmp_path)
    workbook_path = next(path for path in result.paths if path.suffix == ".xlsx")
    workbook = load_workbook(workbook_path, data_only=False)
    assert "Summary" in workbook.sheetnames
    workbook.close()


def test_brief_artifact(tmp_path: Path) -> None:
    result = create_brief_artifact("Prepare for roadmap sync", tmp_path, brief_type="meeting")
    assert result.paths[0].exists()
    assert "## Next Steps" in result.paths[0].read_text(encoding="utf-8")
