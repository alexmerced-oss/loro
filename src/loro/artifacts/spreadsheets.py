from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference

from loro.artifacts.common import ArtifactResult, ensure_output_dir, slugify, title_from_prompt


@dataclass(frozen=True)
class WorkbookPlan:
    title: str
    sheets: list[str]
    assumptions: list[str]


def create_spreadsheet_artifact(prompt: str, output_dir: Path) -> ArtifactResult:
    title = title_from_prompt(prompt, "Loro Workbook")
    slug = slugify(title, "workbook")
    output_dir = ensure_output_dir(output_dir)
    xlsx_path = output_dir / f"{slug}.xlsx"
    csv_path = output_dir / f"{slug}-summary.csv"

    rows = [
        ("Category", "Planned", "Actual", "Variance"),
        ("Scope", 10, 8, "=C2-B2"),
        ("Schedule", 10, 9, "=C3-B3"),
        ("Risk", 10, 6, "=C4-B4"),
    ]

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Summary"
    sheet["A1"] = title
    sheet["A2"] = "Prompt"
    sheet["B2"] = prompt.strip()
    start_row = 4
    for row_index, row in enumerate(rows, start=start_row):
        for column_index, value in enumerate(row, start=1):
            sheet.cell(row=row_index, column=column_index, value=value)

    chart = BarChart()
    chart.title = "Planned vs Actual"
    chart.y_axis.title = "Score"
    chart.x_axis.title = "Category"
    data = Reference(sheet, min_col=2, max_col=3, min_row=start_row, max_row=start_row + 3)
    categories = Reference(sheet, min_col=1, min_row=start_row + 1, max_row=start_row + 3)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(categories)
    sheet.add_chart(chart, "F4")
    workbook.save(xlsx_path)

    csv_path.write_text(
        "\n".join(",".join(str(item) for item in row) for row in rows) + "\n",
        encoding="utf-8",
    )

    # Validate the workbook can be opened after writing.
    load_workbook(xlsx_path, data_only=False).close()

    return ArtifactResult(
        title=title,
        kind="spreadsheet",
        paths=[xlsx_path, csv_path],
        summary=f"Created spreadsheet artifacts: {xlsx_path} and {csv_path}",
    )
