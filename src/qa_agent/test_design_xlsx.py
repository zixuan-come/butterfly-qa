"""Generate an XLSX workbook for test design artifacts without extra packages."""

from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def render_test_design_xlsx(test_design: Any) -> bytes:
    """Render a TestDesign model or mapping as a practical Excel workbook."""
    data = (
        test_design.model_dump(mode="json")
        if hasattr(test_design, "model_dump")
        else test_design
    )
    points = data.get("test_points") or []
    cases = data.get("test_cases") or []
    sheets = [
        {
            "name": "测试用例",
            "columns": [
                ("用例 ID", 16), ("用例标题", 36), ("优先级", 12),
                ("关联需求", 20), ("关联测试点", 20), ("前置条件", 34),
                ("测试数据", 34), ("步骤数", 10), ("结构状态", 14),
            ],
            "rows": [
                [
                    case.get("case_id", ""), case.get("title", ""),
                    case.get("priority", ""), _join(case.get("requirement_refs")),
                    _join(case.get("test_point_refs")), _join(case.get("preconditions")),
                    _join(case.get("test_data")), len(case.get("steps") or []),
                    "结构完整" if case.get("steps") else "结构不完整",
                ]
                for case in cases
            ],
        },
        {
            "name": "测试步骤",
            "columns": [
                ("用例 ID", 16), ("用例标题", 36), ("步骤", 10),
                ("操作", 54), ("预期结果", 54),
            ],
            "rows": [
                [
                    case.get("case_id", ""), case.get("title", ""),
                    step.get("step_no", index), step.get("action", ""),
                    step.get("expected_result", ""),
                ]
                for case in cases
                for index, step in enumerate(case.get("steps") or [], start=1)
            ],
        },
        {
            "name": "测试点",
            "columns": [
                ("测试点 ID", 16), ("关联需求", 20), ("类型", 14),
                ("风险", 12), ("测试点描述", 60),
            ],
            "rows": [
                [
                    point.get("test_point_id", ""), _join(point.get("requirement_refs")),
                    point.get("category", ""), point.get("risk", ""),
                    point.get("description", ""),
                ]
                for point in points
            ],
        },
    ]

    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml(len(sheets)))
        archive.writestr("_rels/.rels", _package_rels_xml())
        archive.writestr("xl/workbook.xml", _workbook_xml(sheets))
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            _workbook_rels_xml(len(sheets)),
        )
        archive.writestr("xl/styles.xml", _styles_xml())
        for index, sheet in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(sheet))
    return stream.getvalue()


def save_test_design_xlsx(test_design: Any, path: Path) -> Path:
    """Render and persist an XLSX file at the supplied artifact path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_test_design_xlsx(test_design))
    return path


def _join(values: Iterable[Any] | None) -> str:
    return "；".join(str(value) for value in (values or []))


def _workbook_xml(sheets: list[dict[str, Any]]) -> str:
    sheet_xml = "".join(
        f'<sheet name="{escape(sheet["name"])}" sheetId="{index}" r:id="rId{index}"/>'
        for index, sheet in enumerate(sheets, start=1)
    )
    return _xml(
        f'<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">'
        "<bookViews><workbookView/></bookViews>"
        f"<sheets>{sheet_xml}</sheets></workbook>"
    )


def _workbook_rels_xml(sheet_count: int) -> str:
    relationships = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    relationships += (
        f'<Relationship Id="rId{sheet_count + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    return _xml(f'<Relationships xmlns="{PACKAGE_REL_NS}">{relationships}</Relationships>')


def _content_types_xml(sheet_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return _xml(
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{overrides}</Types>"
    )


def _package_rels_xml() -> str:
    return _xml(
        f'<Relationships xmlns="{PACKAGE_REL_NS}">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def _styles_xml() -> str:
    return _xml(
        f'<styleSheet xmlns="{MAIN_NS}">'
        '<numFmts count="0"/>'
        '<fonts count="2"><font><sz val="11"/><name val="等线"/></font><font><b/><sz val="11"/><name val="等线"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FFD9E2F3"/></left><right style="thin"><color rgb="FFD9E2F3"/></right><top style="thin"><color rgb="FFD9E2F3"/></top><bottom style="thin"><color rgb="FFD9E2F3"/></bottom><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0"><alignment vertical="top" wrapText="1"/></xf></cellXfs>'
        '<cellStyles count="1"><cellStyle name="常规" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )


def _sheet_xml(sheet: dict[str, Any]) -> str:
    columns = sheet["columns"]
    rows = [_row_xml(1, [title for title, _width in columns], header=True)]
    rows.extend(
        _row_xml(index, row, header=False)
        for index, row in enumerate(sheet["rows"], start=2)
    )
    last_column = _column_name(len(columns))
    last_row = max(1, len(sheet["rows"]) + 1)
    column_xml = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, (_title, width) in enumerate(columns, start=1)
    )
    return _xml(
        f'<worksheet xmlns="{MAIN_NS}"><dimension ref="A1:{last_column}{last_row}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>'
        f"<cols>{column_xml}</cols><sheetData>{''.join(rows)}</sheetData>"
        f'<autoFilter ref="A1:{last_column}{last_row}"/><pageMargins left="0.25" right="0.25" top="0.5" bottom="0.5" header="0.3" footer="0.3"/></worksheet>'
    )


def _row_xml(row_number: int, values: list[Any], *, header: bool) -> str:
    cells = "".join(
        _cell_xml(_column_name(index), row_number, value, style_id=1 if header else 2)
        for index, value in enumerate(values, start=1)
    )
    return f'<row r="{row_number}">{cells}</row>'


def _cell_xml(column: str, row_number: int, value: Any, *, style_id: int) -> str:
    reference = f"{column}{row_number}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}" s="{style_id}"><v>{value}</v></c>'
    text = escape(str(value if value is not None else ""))
    return f'<c r="{reference}" s="{style_id}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xml(content: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + content