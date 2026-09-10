"""Unit tests for the offline execution workbook parser."""

from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from qa_agent import execution_xlsx
from qa_agent.execution_xlsx import ExecutionXlsxError, parse_execution_xlsx
from qa_agent.schemas import (
    ArtifactMeta,
    ArtifactStatus,
    ExecutionResult,
    TestCase as CaseModel,
    TestDesign as DesignModel,
    TestPoint as PointModel,
    TestStep as StepModel,
)

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_HEADER = [
    "用例 ID", "用例版本", "执行结果", "实际结果",
    "执行环境", "执行人", "执行时间", "缺陷 ID", "证据说明", "执行备注",
]


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell_xml(column: str, row_number: int, value) -> str:
    reference = f"{column}{row_number}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}"><v>{value}</v></c>'
    text = str(value)
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(rows: list[tuple[int, list]]) -> str:
    body = ""
    for row_number, cells in rows:
        cell_xml = "".join(
            _cell_xml(_column_name(index), row_number, value)
            for index, value in enumerate(cells, start=1)
        )
        body += f'<row r="{row_number}">{cell_xml}</row>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{_MAIN_NS}"><sheetData>{body}</sheetData></worksheet>'
    )


def _build_workbook(sheets: list[tuple[str, list[tuple[int, list]]]]) -> bytes:
    """Assemble a minimal .xlsx from explicit rows with real row numbers."""

    sheet_refs = "".join(
        f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _rows) in enumerate(sheets, start=1)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<workbook xmlns="{_MAIN_NS}" xmlns:r="{_REL_NS}">'
        f"<sheets>{sheet_refs}</sheets></workbook>"
    )
    rels = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{_PACKAGE_REL_NS}">{rels}</Relationships>'
    )
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        for index, (_name, rows) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows))
    return stream.getvalue()


def _notes_rows(design_id: str = "design-001", version: int = 1) -> list[tuple[int, list]]:
    return [
        (1, ["字段", "说明"]),
        (2, ["测试设计 ID", design_id]),
        (3, ["测试设计版本", version]),
    ]


def _cases_sheet(rows: list[tuple[int, list]]) -> tuple[str, list[tuple[int, list]]]:
    return ("测试用例", [(1, _HEADER)] + rows)


def _workbook(case_rows, *, design_id="design-001", version=1) -> bytes:
    return _build_workbook([
        _cases_sheet(case_rows),
        ("执行说明", _notes_rows(design_id, version)),
    ])


def make_design(case_ids=("TC-001", "TC-002")) -> DesignModel:
    timestamp = datetime.now(timezone.utc)
    return DesignModel(
        meta=ArtifactMeta(
            artifact_id="design-001",
            artifact_type="test_design",
            project_id="demo-project",
            status=ArtifactStatus.APPROVED,
            created_by="test-agent",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        test_points=[
            PointModel(
                test_point_id="TP-001",
                requirement_refs=["REQ-001"],
                category="normal",
                description="保存地址",
            )
        ],
        test_cases=[
            CaseModel(
                case_id=case_id,
                requirement_refs=["REQ-001"],
                test_point_refs=["TP-001"],
                title=f"用例 {case_id}",
                priority="P1",
                steps=[StepModel(step_no=1, action="操作", expected_result="通过")],
            )
            for case_id in case_ids
        ],
    )


def _row(case_id, version, result, actual, *, executed_at="", executor="", environment="", defect="", evidence="", notes=""):
    return [case_id, version, result, actual, environment, executor, executed_at, defect, evidence, notes]


def test_parses_complete_workbook_into_records():
    workbook = _workbook([
        (2, _row("TC-001", 1, "通过", "符合预期", executor="tester-a", environment="SIT")),
        (3, _row("TC-002", 1, "失败", "报错", defect="BUG-1；BUG-2", evidence="log.txt")),
    ])

    records = parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert [(r.case_id, r.result) for r in records] == [
        ("TC-001", ExecutionResult.PASSED),
        ("TC-002", ExecutionResult.FAILED),
    ]
    assert records[0].executed_by == "tester-a"
    assert records[0].environment == "SIT"
    assert records[1].defect_refs == ["BUG-1", "BUG-2"]
    assert records[1].evidence_notes == ["log.txt"]
    # executor/environment left blank fall back to upload metadata.
    assert records[1].executed_by == "uploader"
    assert records[1].environment == "未填写环境"


def test_reads_excel_serial_date_as_utc_timestamp():
    # 45900.5 is 2025-08-31 12:00 UTC — Excel stores typed dates as day offsets.
    workbook = _workbook([
        (2, _row("TC-001", 1, "通过", "ok", executed_at=45900.5)),
        (3, _row("TC-002", 1, "通过", "ok")),
    ])

    records = parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert records[0].executed_at == datetime(2025, 8, 31, 12, 0, tzinfo=timezone.utc)


def test_parses_textual_datetime_variants():
    workbook = _workbook([
        (2, _row("TC-001", 1, "通过", "ok", executed_at="2026/01/02 08:30")),
        (3, _row("TC-002", 1, "通过", "ok")),
    ])

    records = parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert records[0].executed_at == datetime(2026, 1, 2, 8, 30, tzinfo=timezone.utc)


def test_missing_cases_sheet_is_rejected():
    workbook = _build_workbook([("执行说明", _notes_rows())])

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert "测试用例" in str(exc.value)


def test_missing_required_column_is_rejected_with_issue():
    header = [column for column in _HEADER if column != "实际结果"]
    workbook = _build_workbook([
        ("测试用例", [(1, header), (2, ["TC-001", 1, "通过", "SIT"])]),
        ("执行说明", _notes_rows()),
    ])

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(("TC-001",)), submitted_by="uploader")

    assert any(issue["column"] == "实际结果" for issue in exc.value.issues)


def test_unrecognized_result_is_reported_by_row():
    workbook = _workbook([
        (2, _row("TC-001", 1, "OK", "ok")),
        (3, _row("TC-002", 1, "通过", "ok")),
    ])

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert {"row": 2, "column": "执行结果", "message": "必须填写：通过、失败、阻塞或跳过"} in exc.value.issues


def test_empty_actual_result_is_reported_by_row():
    workbook = _workbook([
        (2, _row("TC-001", 1, "通过", "")),
        (3, _row("TC-002", 1, "通过", "ok")),
    ])

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert any(i["row"] == 2 and i["column"] == "实际结果" for i in exc.value.issues)


def test_duplicate_case_row_is_reported():
    workbook = _workbook([
        (2, _row("TC-001", 1, "通过", "ok")),
        (3, _row("TC-001", 1, "失败", "再来一次")),
        (4, _row("TC-002", 1, "通过", "ok")),
    ])

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert any("重复提交" in i["message"] and i["row"] == 3 for i in exc.value.issues)


def test_unknown_case_is_reported():
    workbook = _workbook([
        (2, _row("TC-001", 1, "通过", "ok")),
        (3, _row("TC-999", 1, "通过", "ok")),
        (4, _row("TC-002", 1, "通过", "ok")),
    ])

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert any("不属于当前测试设计" in i["message"] and i["row"] == 3 for i in exc.value.issues)


def test_missing_case_result_is_reported_when_rows_are_identifiable():
    workbook = _workbook([
        (2, _row("TC-001", 1, "通过", "ok")),
    ])

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert any("缺少用例 TC-002:v1" in i["message"] for i in exc.value.issues)


def test_row_numbers_track_deleted_blank_rows():
    # A tester cleared row 3, so Excel emits rows 2 and 4 with no row 3 element.
    workbook = _workbook([
        (2, _row("TC-001", 1, "通过", "ok")),
        (4, _row("TC-002", 1, "OK", "ok")),
    ])

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    # The bad result must be pinned to row 4, not to a positional index.
    assert any(i["row"] == 4 and i["column"] == "执行结果" for i in exc.value.issues)


def test_stale_template_is_rejected_before_row_level_errors():
    workbook = _workbook(
        [(2, _row("OLD-1", 1, "通过", "ok"))],
        design_id="design-000",
    )

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert "重新下载模板" in str(exc.value)
    assert all(i["row"] != 2 for i in exc.value.issues)


def test_stale_template_version_is_rejected():
    workbook = _workbook(
        [(2, _row("TC-001", 1, "通过", "ok")), (3, _row("TC-002", 1, "通过", "ok"))],
        version=2,
    )

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert "旧版模板" in str(exc.value)


def test_non_xlsx_bytes_are_rejected():
    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(b"not a zip", test_design=make_design(), submitted_by="uploader")

    assert "工作簿" in str(exc.value)


def test_oversized_sheet_entry_is_rejected(monkeypatch):
    # Leave the total budget generous so workbook.xml/rels/notes pass, and lower
    # only the per-entry cap so the failure genuinely trips on the oversized
    # cases sheet this test names — not on an earlier, smaller entry.
    monkeypatch.setattr(execution_xlsx, "_MAX_ENTRY_UNCOMPRESSED_BYTES", 700)
    monkeypatch.setattr(execution_xlsx, "_MAX_TOTAL_UNCOMPRESSED_BYTES", 10 * 1024 * 1024)
    workbook = _workbook([
        (2, _row("TC-001", 1, "通过", "x" * 5000)),
        (3, _row("TC-002", 1, "通过", "ok")),
    ])

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert "体积超出上限" in str(exc.value)


def test_size_budget_counts_bytes_actually_read(monkeypatch):
    # The cap must be enforced against bytes actually decompressed and read,
    # not a declared central-directory size — a large entry is rejected even
    # when the per-entry check would let it start.
    monkeypatch.setattr(execution_xlsx, "_MAX_ENTRY_UNCOMPRESSED_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(execution_xlsx, "_MAX_TOTAL_UNCOMPRESSED_BYTES", 2048)
    workbook = _workbook([
        (2, _row("TC-001", 1, "通过", "x" * 20000)),
        (3, _row("TC-002", 1, "通过", "ok")),
    ])

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert "体积超出上限" in str(exc.value)


def _build_shared_string_workbook(case_rows) -> bytes:
    """Assemble an .xlsx whose string cells use the shared-string table (t="s"),
    exactly as Excel/WPS save them — the parser's shared-string branch."""

    pool: list[str] = []

    def intern(text: str) -> int:
        if text not in pool:
            pool.append(text)
        return pool.index(text)

    def cell_xml(column: str, row_number: int, value) -> str:
        reference = f"{column}{row_number}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{reference}"><v>{value}</v></c>'
        index = intern(str(value))
        return f'<c r="{reference}" t="s"><v>{index}</v></c>'

    def sheet_xml(rows) -> str:
        body = ""
        for row_number, cells in rows:
            cell_body = "".join(
                cell_xml(_column_name(index), row_number, value)
                for index, value in enumerate(cells, start=1)
            )
            body += f'<row r="{row_number}">{cell_body}</row>'
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<worksheet xmlns="{_MAIN_NS}"><sheetData>{body}</sheetData></worksheet>'
        )

    sheets = [_cases_sheet(case_rows), ("执行说明", _notes_rows())]
    sheet_refs = "".join(
        f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _rows) in enumerate(sheets, start=1)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<workbook xmlns="{_MAIN_NS}" xmlns:r="{_REL_NS}">'
        f"<sheets>{sheet_refs}</sheets></workbook>"
    )
    rels = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{_PACKAGE_REL_NS}">{rels}</Relationships>'
    )
    # Render sheets first so the shared-string pool is fully populated.
    rendered = [(index, sheet_xml(rows)) for index, (_name, rows) in enumerate(sheets, start=1)]
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<sst xmlns="{_MAIN_NS}" count="{len(pool)}" uniqueCount="{len(pool)}">'
        + "".join(f"<si><t xml:space=\"preserve\">{text}</t></si>" for text in pool)
        + "</sst>"
    )
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        archive.writestr("xl/sharedStrings.xml", shared_xml)
        for index, sheet_body in rendered:
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet_body)
    return stream.getvalue()


def test_parses_shared_string_cells():
    workbook = _build_shared_string_workbook([
        (2, _row("TC-001", 1, "通过", "符合预期", executor="tester-a", environment="SIT")),
        (3, _row("TC-002", 1, "失败", "报错")),
    ])

    records = parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert [(r.case_id, r.result) for r in records] == [
        ("TC-001", ExecutionResult.PASSED),
        ("TC-002", ExecutionResult.FAILED),
    ]
    assert records[0].executed_by == "tester-a"
    assert records[0].environment == "SIT"


@pytest.mark.parametrize("bad_version", ["inf", "-inf", "nan", "1e999"])
def test_non_finite_case_version_is_rejected_cleanly(bad_version):
    workbook = _workbook([
        (2, _row("TC-001", bad_version, "通过", "ok")),
        (3, _row("TC-002", 1, "通过", "ok")),
    ])

    with pytest.raises(ExecutionXlsxError) as exc:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")

    assert any(
        issue["column"] == "用例版本" and "正整数" in issue["message"]
        for issue in exc.value.issues
    )


@pytest.mark.parametrize("bad_label", ["inf", "1e999"])
def test_non_finite_template_version_label_is_rejected_cleanly(bad_label):
    # A non-finite 测试设计版本 label must not crash int(float(...)) with
    # OverflowError; it should fall through to a normal template mismatch.
    workbook = _workbook(
        [(2, _row("TC-001", 1, "通过", "ok")), (3, _row("TC-002", 1, "通过", "ok"))],
        version=bad_label,
    )

    # Parsing must not raise OverflowError; either it accepts (label ignored) or
    # rejects with a template-version issue — never an unhandled 500.
    try:
        parse_execution_xlsx(workbook, test_design=make_design(), submitted_by="uploader")
    except ExecutionXlsxError as exc:
        assert "模板" in str(exc.value)
