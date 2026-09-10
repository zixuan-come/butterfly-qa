"""Parse the editable Excel template used for manual test execution."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

from .schemas import ExecutionRecord, ExecutionResult, TestDesign


class ExecutionXlsxError(ValueError):
    """Raised when an execution workbook is not a valid complete submission."""

    def __init__(self, message: str, issues: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_RESULTS = {
    "通过": ExecutionResult.PASSED,
    "passed": ExecutionResult.PASSED,
    "pass": ExecutionResult.PASSED,
    "失败": ExecutionResult.FAILED,
    "failed": ExecutionResult.FAILED,
    "fail": ExecutionResult.FAILED,
    "阻塞": ExecutionResult.BLOCKED,
    "blocked": ExecutionResult.BLOCKED,
    "block": ExecutionResult.BLOCKED,
    "跳过": ExecutionResult.SKIPPED,
    "skipped": ExecutionResult.SKIPPED,
    "skip": ExecutionResult.SKIPPED,
}
_REQUIRED_COLUMNS = ("用例 ID", "用例版本", "执行结果", "实际结果")
_CASES_SHEET = "测试用例"
_NOTES_SHEET = "执行说明"
_DESIGN_ID_LABEL = "测试设计 ID"
_DESIGN_VERSION_LABEL = "测试设计版本"

# An uploaded workbook is untrusted input; a small archive can expand into a huge
# sheet, so every entry is read under a shared uncompressed-size budget.
_MAX_TOTAL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
_MAX_ENTRY_UNCOMPRESSED_BYTES = 32 * 1024 * 1024

# Excel stores dates as day offsets from 1900-01-00, with a phantom 1900-02-29.
# Anchoring at 1899-12-30 is exact for every serial from 61 (1900-03-01) onwards.
_EXCEL_EPOCH = datetime(1899, 12, 30, tzinfo=timezone.utc)
_MAX_EXCEL_SERIAL = 2958466


def parse_execution_xlsx(
    source: bytes | bytearray | str | Path,
    *,
    test_design: TestDesign,
    submitted_by: str,
    uploaded_at: datetime | None = None,
) -> list[ExecutionRecord]:
    """Parse and validate one workbook against the active test design."""

    try:
        workbook = _read_workbook(source)
    except ExecutionXlsxError:
        raise
    except (BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
        raise ExecutionXlsxError(
            "执行结果文件不是有效的 .xlsx 工作簿",
            [{"row": 1, "column": "文件", "message": str(exc)}],
        ) from exc

    rows = workbook.get(_CASES_SHEET)
    if rows is None:
        raise ExecutionXlsxError(
            "工作簿缺少“测试用例”页签",
            [{"row": 1, "column": "页签", "message": "必须保留下载模板中的“测试用例”页签"}],
        )

    _require_matching_template(workbook.get(_NOTES_SHEET), test_design)

    header_row, header = _read_header(rows)
    if header is None:
        raise ExecutionXlsxError(
            "“测试用例”页签没有表头",
            [{"row": 1, "column": "页签", "message": "请重新下载模板后填写"}],
        )
    issues: list[dict[str, Any]] = [
        {"row": header_row, "column": column, "message": "缺少必需列"}
        for column in _REQUIRED_COLUMNS
        if column not in header
    ]
    if issues:
        raise ExecutionXlsxError("执行结果模板列不完整", issues)

    expected_cases = {(case.case_id, case.version) for case in test_design.test_cases}
    records: list[ExecutionRecord] = []
    seen: set[tuple[str, int]] = set()
    unidentified_rows = False
    timestamp = uploaded_at or datetime.now(timezone.utc)
    for row_number, row in rows:
        if row_number <= header_row or not any(cell.strip() for cell in row):
            continue
        values = {column: _cell(row, index) for column, index in header.items()}
        case_id = values.get("用例 ID", "").strip()
        if not case_id:
            issues.append({"row": row_number, "column": "用例 ID", "message": "不能为空"})
            unidentified_rows = True
            continue
        case_version = _parse_positive_int(values.get("用例版本", ""), row_number, "用例版本", issues)
        if case_version is None:
            unidentified_rows = True
            continue
        key = (case_id, case_version)
        if key in seen:
            issues.append({"row": row_number, "column": "用例 ID", "message": f"重复提交 {case_id}:v{case_version}"})
            continue
        seen.add(key)
        if key not in expected_cases:
            issues.append({"row": row_number, "column": "用例 ID", "message": f"不属于当前测试设计：{case_id}:v{case_version}"})
            continue
        result = _RESULTS.get(values.get("执行结果", "").strip().lower())
        if result is None:
            issues.append({"row": row_number, "column": "执行结果", "message": "必须填写：通过、失败、阻塞或跳过"})
            continue
        actual_result = values.get("实际结果", "").strip()
        if not actual_result:
            issues.append({"row": row_number, "column": "实际结果", "message": "不能为空"})
            continue
        executed_at = _parse_datetime(values.get("执行时间", ""), row_number, "执行时间", issues, timestamp)
        records.append(ExecutionRecord(
            record_id=f"record-{case_id}-v{case_version}", case_id=case_id,
            case_version=case_version,
            environment=values.get("执行环境", "").strip() or "未填写环境",
            executed_by=values.get("执行人", "").strip() or submitted_by,
            executed_at=executed_at, result=result, actual_result=actual_result,
            defect_refs=_split_values(values.get("缺陷 ID", "")),
            evidence_notes=_split_values(values.get("证据说明", "")),
            notes=_split_values(values.get("执行备注", "")),
        ))

    if not unidentified_rows:
        for case_id, version in sorted(expected_cases - seen):
            issues.append({"row": None, "column": "用例 ID", "message": f"缺少用例 {case_id}:v{version} 的执行结果"})
    if issues:
        raise ExecutionXlsxError("执行结果校验失败，请按行号和列名修正 Excel", issues)
    if not records:
        raise ExecutionXlsxError(
            "执行结果文件没有可提交的用例",
            [{"row": header_row + 1, "column": _CASES_SHEET, "message": "至少填写一条完整执行结果"}],
        )
    return records


def _require_matching_template(
    rows: list[tuple[int, list[str]]] | None,
    test_design: TestDesign,
) -> None:
    """Reject a stale template before it produces one error per case row."""

    if not rows:
        return
    labels = {row[0].strip(): row[1].strip() for _, row in rows if len(row) >= 2}
    design_id = labels.get(_DESIGN_ID_LABEL, "")
    design_version = labels.get(_DESIGN_VERSION_LABEL, "")
    if design_id and design_id != test_design.meta.artifact_id:
        raise ExecutionXlsxError(
            "执行结果文件不属于当前测试设计，请重新下载模板",
            [{
                "row": None,
                "column": _DESIGN_ID_LABEL,
                "message": f"文件为 {design_id}，当前为 {test_design.meta.artifact_id}",
            }],
        )
    if design_version:
        try:
            parsed_float = float(design_version)
            parsed_version = int(parsed_float) if math.isfinite(parsed_float) else None
        except (TypeError, ValueError, OverflowError):
            parsed_version = None
        if parsed_version is not None and parsed_version != test_design.meta.version:
            raise ExecutionXlsxError(
                "执行结果文件使用的是旧版模板，请重新下载模板",
                [{
                    "row": None,
                    "column": _DESIGN_VERSION_LABEL,
                    "message": f"文件为 v{parsed_version}，当前为 v{test_design.meta.version}",
                }],
            )


def _read_header(rows: list[tuple[int, list[str]]]) -> tuple[int, dict[str, int] | None]:
    for row_number, row in rows:
        header = {value.strip(): index for index, value in enumerate(row) if value.strip()}
        if header:
            return row_number, header
    return 1, None


class _SizeBudget:
    """Cap how many uncompressed bytes one workbook is allowed to expand into."""

    def __init__(self, limit: int) -> None:
        self.remaining = limit

    def _reject(self) -> None:
        raise ExecutionXlsxError(
            "执行结果文件解压后体积超出上限",
            [{"row": 1, "column": "文件", "message": "请上传由系统下载的模板，不要附加大量额外内容"}],
        )

    def take(self, size: int) -> None:
        if size > _MAX_ENTRY_UNCOMPRESSED_BYTES or size > self.remaining:
            self._reject()
        self.remaining -= size


def _read_workbook(source: bytes | bytearray | str | Path) -> dict[str, list[tuple[int, list[str]]]]:
    archive_source: Any = source if isinstance(source, (str, Path)) else BytesIO(bytes(source))
    with ZipFile(archive_source) as archive:
        budget = _SizeBudget(_MAX_TOTAL_UNCOMPRESSED_BYTES)
        shared_strings = _shared_strings(archive, budget)
        workbook = ET.fromstring(_read_entry(archive, "xl/workbook.xml", budget))
        relationships = ET.fromstring(_read_entry(archive, "xl/_rels/workbook.xml.rels", budget))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships.findall(f"{{{_PACKAGE_REL_NS}}}Relationship")}
        result: dict[str, list[tuple[int, list[str]]]] = {}
        for sheet in workbook.findall(f"{{{_MAIN_NS}}}sheets/{{{_MAIN_NS}}}sheet"):
            name = sheet.attrib.get("name", "")
            relationship_id = sheet.attrib.get(f"{{{_REL_NS}}}id")
            target = targets.get(relationship_id or "")
            if not target:
                continue
            target_path = target.lstrip("/")
            if not target_path.startswith("xl/"):
                target_path = f"xl/{target_path}"
            result[name] = _sheet_rows(_read_entry(archive, target_path, budget), shared_strings)
        return result


def _read_entry(archive: ZipFile, name: str, budget: _SizeBudget) -> bytes:
    # Trust the actual decompressed byte count, not the central-directory
    # file_size (which an attacker controls) — read incrementally and stop the
    # moment the running total would blow the budget, so a crafted entry that
    # lies about its size still cannot expand past the cap.
    info = archive.getinfo(name)
    if info.file_size > _MAX_ENTRY_UNCOMPRESSED_BYTES:
        budget._reject()
    chunk_size = 64 * 1024
    chunks: list[bytes] = []
    with archive.open(name) as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            budget.take(len(chunk))
            chunks.append(chunk)
    return b"".join(chunks)


def _shared_strings(archive: ZipFile, budget: _SizeBudget) -> list[str]:
    try:
        content = _read_entry(archive, "xl/sharedStrings.xml", budget)
    except KeyError:
        return []
    root = ET.fromstring(content)
    return ["".join(node.itertext()) for node in root.findall(f"{{{_MAIN_NS}}}si")]


def _sheet_rows(content: bytes, shared_strings: list[str]) -> list[tuple[int, list[str]]]:
    """Return each row with the spreadsheet row number Excel recorded for it."""

    root = ET.fromstring(content)
    rows: list[tuple[int, list[str]]] = []
    previous_number = 0
    for row in root.findall(f".//{{{_MAIN_NS}}}row"):
        try:
            row_number = int(row.attrib.get("r", ""))
        except ValueError:
            row_number = 0
        if row_number < 1:
            row_number = previous_number + 1
        previous_number = row_number
        cells: dict[int, str] = {}
        for cell in row.findall(f"{{{_MAIN_NS}}}c"):
            column = _column_index(cell.attrib.get("r", "A1"))
            value_node = cell.find(f"{{{_MAIN_NS}}}v")
            inline_node = cell.find(f"{{{_MAIN_NS}}}is")
            if inline_node is not None:
                value = "".join(inline_node.itertext())
            elif value_node is None:
                value = ""
            else:
                value = value_node.text or ""
                if cell.attrib.get("t") == "s":
                    index = int(value or 0)
                    value = shared_strings[index] if index < len(shared_strings) else ""
            cells[column] = value
        width = max(cells, default=-1) + 1
        rows.append((row_number, [cells.get(index, "") for index in range(width)]))
    return rows


def _column_index(reference: str) -> int:
    letters = "".join(character for character in reference if character.isalpha()).upper()
    index = 0
    for character in letters:
        index = index * 26 + ord(character) - ord("A") + 1
    return index - 1


def _cell(row: list[str], index: int) -> str:
    return row[index] if index < len(row) else ""


def _parse_positive_int(value: str, row: int, column: str, issues: list[dict[str, Any]]) -> int | None:
    text = value.strip()
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        issues.append({"row": row, "column": column, "message": "必须是正整数"})
        return None
    if not math.isfinite(parsed) or parsed < 1 or parsed != int(parsed):
        issues.append({"row": row, "column": column, "message": "必须是正整数"})
        return None
    return int(parsed)


def _parse_datetime(value: str, row: int, column: str, issues: list[dict[str, Any]], fallback: datetime) -> datetime:
    text = value.strip()
    if not text:
        return fallback
    serial = _parse_excel_serial(text)
    if serial is not None:
        return serial
    normalized = text.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(normalized, pattern)
                break
            except ValueError:
                pass
        if parsed is None:
            issues.append({"row": row, "column": column, "message": "时间格式应为 YYYY-MM-DD HH:MM[:SS]"})
            return fallback
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_excel_serial(text: str) -> datetime | None:
    """Excel writes a typed date cell as a numeric day offset, not as text."""

    try:
        serial = float(text)
    except ValueError:
        return None
    if not 1 <= serial < _MAX_EXCEL_SERIAL:
        return None
    return _EXCEL_EPOCH + timedelta(days=serial)


def _split_values(value: str) -> list[str]:
    normalized = value.replace("\r", "\n").replace("；", "\n").replace("，", "\n").replace(",", "\n")
    return [item.strip() for item in normalized.split("\n") if item.strip()]
