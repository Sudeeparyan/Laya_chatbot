from pathlib import Path

from openpyxl import Workbook

from app.services.markdown_converter import convert_upload


def test_excel_conversion_removes_blank_artifacts(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Benefits"
    sheet.append([None, None, None])
    sheet.append([None, "Plan", "Cover"])
    sheet.append([None, "Essential", "Full cover"])
    sheet.append([None, None, None])
    source = tmp_path / "benefits.xlsx"
    workbook.save(source)

    response = convert_upload(source, original_filename="benefits.xlsx", department="Claims")

    assert "NaN" not in response.markdown
    assert "Unnamed" not in response.markdown
    assert "## Sheet: Benefits" in response.markdown
    # With ≤3 data rows the converter uses key-value format for better AI comprehension
    assert "**Plan**: Essential" in response.markdown
    assert "**Cover**: Full cover" in response.markdown
    assert "department: \"Claims\"" in response.markdown
    assert Path(response.output_file).exists()