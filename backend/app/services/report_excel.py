"""Excel workbook for a persisted forecast report."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.services.report_builder import UNAVAILABLE, build_excel_forecast_model

HEADER_FILL = PatternFill("solid", fgColor="071A33")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=16, color="071A33")
CATEGORY_FONT = Font(bold=True, size=12, color="071A33")
TOTAL_FONT = Font(bold=True, color="071A33")
CATEGORY_FILL = PatternFill("solid", fgColor="D6E3F0")
TOTAL_FILL = PatternFill("solid", fgColor="F2F2F2")
THIN = Border(
    left=Side(style="thin", color="BFBFBF"),
    right=Side(style="thin", color="BFBFBF"),
    top=Side(style="thin", color="BFBFBF"),
    bottom=Side(style="thin", color="BFBFBF"),
)
SECTION_FILL = PatternFill("solid", fgColor="023E8A")
SECTION_FONT = Font(bold=True, color="FFFFFF", size=12)
AMOUNT_FORMAT = "#,##0.00;(#,##0.00)"
SHEET_NAME = "Budget Forecast"
SHEET_NAMES = (SHEET_NAME,)


def _set_widths(sheet: Worksheet, widths: list[int]) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _write_numeric_or_blank(cell, value: Any) -> None:
    if value == UNAVAILABLE or value is None:
        cell.value = None
        cell.alignment = Alignment(horizontal="right", vertical="center")
        return
    cell.value = float(value)
    cell.number_format = AMOUNT_FORMAT
    cell.alignment = Alignment(horizontal="right", vertical="center")


def render_forecast_excel(report: dict[str, Any]) -> bytes:
    workbook = Workbook()
    _write_budget_forecast(workbook.active, report)
    extra = [name for name in workbook.sheetnames if name != SHEET_NAME]
    if extra:
        raise AssertionError("Excel export must contain only the Budget Forecast worksheet.")
    if workbook.sheetnames != [SHEET_NAME]:
        raise AssertionError("Excel workbook must contain exactly one worksheet: Budget Forecast.")
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_budget_forecast(sheet: Worksheet, report: dict[str, Any]) -> None:
    sheet.title = SHEET_NAME
    model = report.get("excel_forecast") or build_excel_forecast_model({
        "requested_start_month": report.get("requested_start_month"),
        "requested_end_month": report.get("requested_end_month"),
        "category": (report.get("header") or {}).get("category"),
        "selected_accounts": report.get("selected_accounts") or [],
        "monthly_forecasts": report.get("monthly_forecasts") or report.get("chart_months") or [],
        "account_monthly_forecasts": report.get("account_monthly_forecasts") or [],
        "budget_code_forecasts": report.get("budget_code_forecasts") or [],
    })
    months = list(model.get("months") or [])
    headers = ["ACT CODE", "ACT NAME", *list(model.get("month_headers") or []), "YTD"]
    last_col = len(headers)
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(last_col, 1))
    title = sheet.cell(1, 1, model.get("title") or "AI-Based Budget Forecast")
    title.font = TITLE_FONT
    title.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 26
    sections = list(model.get("sections") or [])
    partial = any(
        item.get("unavailable")
        for section in sections
        for item in (section.get("rows") or [])
    )
    if partial:
        note = sheet.cell(
            2,
            1,
            "Forecast unavailable codes have blank amounts. Those blanks are not zeros. "
            "Totals exclude them and are partial.",
        )
        note.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        sheet.row_dimensions[2].height = 32
    row = 3
    for section in sections:
        category = str(section.get("category") or "")
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
        heading = sheet.cell(row, 1, category)
        heading.font = CATEGORY_FONT
        heading.fill = CATEGORY_FILL
        heading.alignment = Alignment(vertical="center")
        for column in range(1, last_col + 1):
            sheet.cell(row, column).fill = CATEGORY_FILL
            sheet.cell(row, column).border = THIN
        sheet.row_dimensions[row].height = 20
        row += 1
        for column, label in enumerate(headers, start=1):
            cell = sheet.cell(row, column, label)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = THIN
        row += 1
        section_partial = any(item.get("unavailable") for item in (section.get("rows") or []))
        for item in section.get("rows") or []:
            code_cell = sheet.cell(row, 1, str(item.get("budget_code") or ""))
            code_cell.number_format = "@"
            code_cell.alignment = Alignment(horizontal="left", vertical="center")
            account_name = str(item.get("account_name") or "")
            if item.get("unavailable"):
                account_name = f"{account_name} — Forecast unavailable".strip()
            sheet.cell(row, 2, account_name).alignment = Alignment(
                horizontal="left", vertical="center", wrap_text=True
            )
            values = list(item.get("months") or [])
            for index in range(len(months)):
                amount = values[index] if index < len(values) else None
                _write_numeric_or_blank(sheet.cell(row, 3 + index), amount)
            _write_numeric_or_blank(sheet.cell(row, last_col), item.get("ytd"))
            for column in range(1, last_col + 1):
                sheet.cell(row, column).border = THIN
            row += 1
        sheet.cell(row, 1, "TOTAL (partial)" if section_partial else "TOTAL")
        sheet.cell(row, 2, "")
        totals = list(section.get("totals") or [])
        for index in range(len(months)):
            amount = totals[index] if index < len(totals) else None
            _write_numeric_or_blank(sheet.cell(row, 3 + index), amount)
        sheet.cell(row, last_col).value = None
        for column in range(1, last_col + 1):
            sheet.cell(row, column).fill = TOTAL_FILL
            sheet.cell(row, column).border = THIN
            sheet.cell(row, column).font = TOTAL_FONT
        row += 2
    if not sections:
        message = sheet.cell(row, 1, model.get("no_forecast_message") or "No forecast available for the selected category.")
        message.font = Font(bold=True, color="071A33")
        message.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max(last_col, 1))
        sheet.row_dimensions[row].height = 22
        row += 2
    row = _write_coverage_summary(sheet, model, row, last_col)
    row += 1
    _write_unavailable_accounts(sheet, model, row, last_col)
    _set_widths(sheet, [16, 44, *[12] * len(months), 14])
    for index, width in enumerate([18, 44, 28, 18, 36], start=1):
        letter = get_column_letter(index)
        current = sheet.column_dimensions[letter].width or 0
        if width > current:
            sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "A3"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True


def _section_banner(sheet: Worksheet, row: int, last_col: int, title: str) -> None:
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max(last_col, 1))
    cell = sheet.cell(row, 1, title)
    cell.font = SECTION_FONT
    cell.fill = SECTION_FILL
    cell.alignment = Alignment(horizontal="left", vertical="center")
    for column in range(1, max(last_col, 1) + 1):
        sheet.cell(row, column).fill = SECTION_FILL
        sheet.cell(row, column).font = SECTION_FONT
        sheet.cell(row, column).border = THIN
    sheet.row_dimensions[row].height = 22


def _write_coverage_summary(sheet: Worksheet, model: dict[str, Any], row: int, last_col: int) -> int:
    _section_banner(sheet, row, last_col, "FORECAST COVERAGE SUMMARY")
    row += 1
    for column, label in enumerate(("Description", "Budget Codes"), start=1):
        cell = sheet.cell(row, column, label)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN
    row += 1
    for label, count in model.get("coverage_rows") or []:
        description = sheet.cell(row, 1, label)
        description.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        value = sheet.cell(row, 2, int(count))
        value.alignment = Alignment(horizontal="right", vertical="center")
        value.number_format = "0"
        for column in range(1, 3):
            sheet.cell(row, column).border = THIN
        sheet.row_dimensions[row].height = 18
        row += 1
    return row


def _write_unavailable_accounts(sheet: Worksheet, model: dict[str, Any], row: int, last_col: int) -> None:
    _section_banner(sheet, row, last_col, "EXCLUDED & UNAVAILABLE BUDGET CODES")
    row += 1
    accounts = list(model.get("unavailable_accounts") or [])
    if not accounts:
        note = sheet.cell(row, 1, model.get("no_unavailable_message") or "No excluded or unavailable budget codes.")
        note.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max(last_col, 1))
        return
    headers = ["ACT CODE", "ACT NAME", "CATEGORY", "STATUS", "REASON"]
    for column, label in enumerate(headers, start=1):
        cell = sheet.cell(row, column, label)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN
    sheet.row_dimensions[row].height = 18
    row += 1
    for item in accounts:
        code_cell = sheet.cell(row, 1, str(item.get("budget_code") or ""))
        code_cell.number_format = "@"
        code_cell.alignment = Alignment(horizontal="left", vertical="center")
        sheet.cell(row, 2, str(item.get("account_name") or "")).alignment = Alignment(
            horizontal="left", vertical="center", wrap_text=True
        )
        sheet.cell(row, 3, str(item.get("category") or "")).alignment = Alignment(
            horizontal="left", vertical="center", wrap_text=True
        )
        sheet.cell(row, 4, "UNAVAILABLE").alignment = Alignment(horizontal="left", vertical="center")
        sheet.cell(row, 5, str(item.get("reason") or "")).alignment = Alignment(
            horizontal="left", vertical="center", wrap_text=True
        )
        for column in range(1, 6):
            sheet.cell(row, column).border = THIN
        sheet.row_dimensions[row].height = 20
        row += 1
