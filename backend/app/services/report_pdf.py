"""Professional PDF for a persisted forecast report."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.report_builder import (
    UNAVAILABLE,
    is_all_categories_label,
    is_finite_amount,
    iso_to_label,
)
from app.services.report_year_totals import (
    shared_zero_inclusive_domain,
    summarize_selected_forecast_years,
)

NAVY = colors.HexColor("#071a33")
BLUE = colors.HexColor("#1f7ae0")
CYAN = colors.HexColor("#06b6d4")
RED = colors.HexColor("#ef4444")
LINE = colors.HexColor("#dbe4ee")
MUTED = colors.HexColor("#66788a")
GRID = colors.HexColor("#e8eef5")
PLOT_FILL = colors.HexColor("#fbfcfe")
LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "slt-mobitel-logo.svg"


def _finite_or_none(value: Any) -> float | None:
    if not is_finite_amount(value):
        return None
    return float(value)


def _y_ticks(low: float, high: float, count: int = 5) -> list[float]:
    if high < low:
        low, high = high, low
    span = high - low
    if span < 1e-12:
        return [low]
    step = span / max(count - 1, 1)
    return [low + step * index for index in range(count)]


def _format_tick(value: float) -> str:
    if abs(value) >= 100:
        return f"{value:,.0f}"
    if abs(value) >= 10:
        return f"{value:,.1f}"
    return f"{value:,.2f}"


class MonthlyForecastBarChart(Flowable):
    """Monthly forecast bars with optional expected-range whiskers and readable axes."""

    def __init__(self, rows: list[dict[str, Any]], width: float, height: float) -> None:
        super().__init__()
        self.rows = rows
        self.width = width
        self.height = height

    def wrap(self, availWidth, availHeight):  # noqa: N802
        return self.width, self.height

    def draw(self) -> None:
        points: list[tuple[str, float | None, float | None, float | None]] = []
        for row in self.rows:
            label = iso_to_label(str(row.get("month") or ""))
            amount = _finite_or_none(row.get("forecast_amount"))
            lower = _finite_or_none(row.get("lower_bound"))
            upper = _finite_or_none(row.get("upper_bound"))
            points.append((label, amount, lower, upper))
        canvas = self.canv
        left, right, bottom, top = 42, self.width - 6, 34, self.height - 14
        plot_w = right - left
        plot_h = top - bottom

        values: list[float] = []
        for _, amount, lower, upper in points:
            if amount is not None:
                values.append(amount)
            if lower is not None and upper is not None:
                values.extend([lower, upper])
        low, high = shared_zero_inclusive_domain(values, padding_ratio=0.08)
        span = high - low or 1.0

        canvas.setFillColor(PLOT_FILL)
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.6)
        canvas.rect(left, bottom, plot_w, plot_h, stroke=1, fill=1)

        canvas.setStrokeColor(GRID)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 6.5)
        for tick in _y_ticks(low, high):
            y = bottom + ((tick - low) / span) * plot_h
            canvas.setStrokeColor(GRID)
            canvas.setLineWidth(0.4)
            canvas.line(left, y, right, y)
            canvas.setFillColor(MUTED)
            canvas.drawRightString(left - 4, y - 2, _format_tick(tick))

        zero_y = bottom + ((0.0 - low) / span) * plot_h
        if bottom <= zero_y <= top:
            canvas.setStrokeColor(colors.HexColor("#8aa0b8"))
            canvas.setDash(2, 2)
            canvas.setLineWidth(0.7)
            canvas.line(left, zero_y, right, zero_y)
            canvas.setDash()

        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(4, top - 1, "LKR Mn")

        count = max(len(points), 1)
        slot = plot_w / count
        bar_w = min(18.0, max(4.0, slot * 0.55))
        label_step = 1
        if count > 24:
            label_step = 3
        elif count > 14:
            label_step = 2

        for index, (label, amount, lower, upper) in enumerate(points):
            x_center = left + slot * index + slot / 2.0
            if amount is None:
                if index % label_step == 0 or index == count - 1:
                    canvas.setFillColor(MUTED)
                    canvas.setFont("Helvetica", 6)
                    canvas.saveState()
                    canvas.translate(x_center, bottom - 10)
                    canvas.rotate(35)
                    canvas.drawCentredString(0, 0, label)
                    canvas.restoreState()
                continue

            y_amount = bottom + ((amount - low) / span) * plot_h
            bar_bottom = min(zero_y, y_amount)
            bar_top = max(zero_y, y_amount)
            canvas.setFillColor(CYAN if amount >= 0 else colors.HexColor("#c0392b"))
            canvas.rect(x_center - bar_w / 2.0, bar_bottom, bar_w, max(bar_top - bar_bottom, 0.6), stroke=0, fill=1)

            if lower is not None and upper is not None:
                y1 = bottom + ((lower - low) / span) * plot_h
                y2 = bottom + ((upper - low) / span) * plot_h
                canvas.setStrokeColor(colors.HexColor("#67e8f9"))
                canvas.setLineWidth(1.1)
                canvas.line(x_center, y1, x_center, y2)
                canvas.line(x_center - 3, y1, x_center + 3, y1)
                canvas.line(x_center - 3, y2, x_center + 3, y2)

            if index % label_step == 0 or index == count - 1:
                canvas.setFillColor(MUTED)
                canvas.setFont("Helvetica", 6)
                canvas.saveState()
                canvas.translate(x_center, bottom - 10)
                canvas.rotate(35)
                canvas.drawCentredString(0, 0, label)
                canvas.restoreState()


class YearTotalBarChart(Flowable):
    """Single red year-total bar with an independent labeled axis on a shared scale."""

    def __init__(
        self,
        summary: dict[str, Any],
        width: float,
        height: float,
        *,
        domain: tuple[float, float],
    ) -> None:
        super().__init__()
        self.summary = summary
        self.width = width
        self.height = height
        self.domain = domain

    def wrap(self, availWidth, availHeight):  # noqa: N802
        return self.width, self.height

    def draw(self) -> None:
        canvas = self.canv
        title = str(self.summary.get("title") or "")
        range_label = str(self.summary.get("range_label") or "")
        amount = _finite_or_none(self.summary.get("total"))
        low, high = self.domain
        span = high - low or 1.0

        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawCentredString(self.width / 2.0, self.height - 11, title)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawCentredString(self.width / 2.0, self.height - 21, range_label)

        left, right, bottom, top = 34, self.width - 8, 28, self.height - 34
        plot_w = right - left
        plot_h = top - bottom
        canvas.setFillColor(PLOT_FILL)
        canvas.setStrokeColor(LINE)
        canvas.rect(left, bottom, plot_w, plot_h, stroke=1, fill=1)

        canvas.setFont("Helvetica", 6)
        for tick in _y_ticks(low, high, count=4):
            y = bottom + ((tick - low) / span) * plot_h
            canvas.setStrokeColor(GRID)
            canvas.setLineWidth(0.4)
            canvas.line(left, y, right, y)
            canvas.setFillColor(MUTED)
            canvas.drawRightString(left - 3, y - 2, _format_tick(tick))

        zero_y = bottom + ((0.0 - low) / span) * plot_h
        if bottom <= zero_y <= top:
            canvas.setStrokeColor(colors.HexColor("#8aa0b8"))
            canvas.setDash(2, 2)
            canvas.line(left, zero_y, right, zero_y)
            canvas.setDash()

        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(2, top + 2, "LKR Mn")

        if amount is not None:
            y_amount = bottom + ((amount - low) / span) * plot_h
            bar_w = min(36.0, plot_w * 0.42)
            x = left + plot_w / 2.0 - bar_w / 2.0
            bar_bottom = min(zero_y, y_amount)
            bar_top = max(zero_y, y_amount)
            canvas.setFillColor(RED)
            canvas.rect(x, bar_bottom, bar_w, max(bar_top - bar_bottom, 0.8), stroke=0, fill=1)

        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(left + plot_w / 2.0, bottom - 12, str(self.summary.get("year") or ""))


class HorizontalRankBarChart(Flowable):
    """Horizontal bars for Top Budget Codes or category totals."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        width: float,
        height: float,
        *,
        label_key: str,
        amount_key: str = "forecast_amount",
        share_key: str | None = "percent",
        bar_color: colors.Color = BLUE,
    ) -> None:
        super().__init__()
        self.rows = rows
        self.width = width
        self.height = height
        self.label_key = label_key
        self.amount_key = amount_key
        self.share_key = share_key
        self.bar_color = bar_color

    def wrap(self, availWidth, availHeight):  # noqa: N802
        return self.width, self.height

    def draw(self) -> None:
        canvas = self.canv
        items = []
        for row in self.rows:
            amount = _finite_or_none(row.get(self.amount_key))
            if amount is None:
                continue
            items.append(row)
        if not items:
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 8)
            canvas.drawString(8, self.height / 2.0, "No values available")
            return

        max_abs = max(abs(float(row[self.amount_key])) for row in items) or 1.0
        left_label = 118
        right_value = 78
        left = left_label
        right = self.width - right_value
        top = self.height - 6
        row_h = min(18.0, (self.height - 8) / max(len(items), 1))

        for index, row in enumerate(items):
            amount = float(row[self.amount_key])
            y = top - (index + 1) * row_h + 3
            label = str(row.get(self.label_key) or "")
            if len(label) > 34:
                label = label[:31] + "…"
            canvas.setFillColor(NAVY)
            canvas.setFont("Helvetica", 7)
            canvas.drawString(4, y + 3, label)

            bar_max_w = max(right - left, 1)
            bar_w = (abs(amount) / max_abs) * bar_max_w
            canvas.setFillColor(self.bar_color if amount >= 0 else colors.HexColor("#c0392b"))
            if amount >= 0:
                canvas.rect(left, y, max(bar_w, 1.0), 8, stroke=0, fill=1)
            else:
                canvas.rect(left, y, max(bar_w, 1.0), 8, stroke=0, fill=1)

            value_text = _format_amount(amount)
            if self.share_key and row.get(self.share_key) is not None:
                value_text = f"{value_text}  ({_format_share(row.get(self.share_key))})"
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 6.5)
            canvas.drawRightString(self.width - 2, y + 2, value_text)


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "section": ParagraphStyle(
            "ReportSection",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=NAVY,
            spaceBefore=8,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=NAVY,
            leading=12,
        ),
        "muted": ParagraphStyle(
            "ReportMuted",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=MUTED,
            leading=11,
        ),
        "cell": ParagraphStyle(
            "ReportCell",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=NAVY,
            leading=10,
        ),
        "cellHeader": ParagraphStyle(
            "ReportCellHeader",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=colors.white,
            leading=10,
        ),
        "cellHeaderRight": ParagraphStyle(
            "ReportCellHeaderRight",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=colors.white,
            alignment=TA_RIGHT,
            leading=10,
        ),
        "cellRight": ParagraphStyle(
            "ReportCellRight",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=NAVY,
            alignment=TA_RIGHT,
            leading=10,
        ),
        "subtotal": ParagraphStyle(
            "ReportSubtotal",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=NAVY,
            leading=10,
        ),
        "subtotalRight": ParagraphStyle(
            "ReportSubtotalRight",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=NAVY,
            alignment=TA_RIGHT,
            leading=10,
        ),
    }


def _format_amount(value: Any) -> str:
    if value == UNAVAILABLE or value is None:
        return UNAVAILABLE
    try:
        number = float(value)
    except (TypeError, ValueError):
        return UNAVAILABLE
    return f"LKR {number:,.2f} Mn"


def _format_percent(value: Any) -> str:
    if value is None:
        return UNAVAILABLE
    try:
        number = float(value)
    except (TypeError, ValueError):
        return UNAVAILABLE
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def _format_share(value: Any) -> str:
    """Share of total as a percentage — never a growth-style '+' prefix."""
    if value is None:
        return UNAVAILABLE
    try:
        number = float(value)
    except (TypeError, ValueError):
        return UNAVAILABLE
    # Accept either 0–1 fractions or already-scaled percents.
    percent = number * 100 if abs(number) <= 1.0000001 else number
    return f"{percent:.2f}%"


def _logo_flowable(width: float) -> Flowable | None:
    if not LOGO_PATH.exists():
        return None
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPM

        drawing = svg2rlg(str(LOGO_PATH))
        if drawing is None:
            return None
        png = renderPM.drawToString(drawing, fmt="PNG", dpi=120)
        image = Image(ImageReader(BytesIO(png)))
        image.drawWidth = 42 * mm
        image.drawHeight = 16 * mm
        return image
    except Exception:
        try:
            from svglib.svglib import svg2rlg

            drawing = svg2rlg(str(LOGO_PATH))
            if drawing is None:
                return None
            scale = width / max(drawing.width, 1)
            drawing.scale(scale, scale)
            drawing.width *= scale
            drawing.height *= scale
            return drawing
        except Exception:
            return None


def _table(
    data: list[list[Any]],
    col_widths: list[float],
    *,
    emphasize_last_row: bool = False,
    emphasize_rows: list[int] | None = None,
) -> Table:
    table = Table(data, colWidths=col_widths, repeatRows=1)
    styles = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7fb")]),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for row_index in emphasize_rows or []:
        if row_index <= 0 or row_index >= len(data):
            continue
        styles.extend(
            [
                ("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#eef2f7")),
                ("FONTNAME", (0, row_index), (-1, row_index), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, row_index), (-1, row_index), NAVY),
            ]
        )
    if emphasize_last_row and len(data) > 1:
        styles.extend(
            [
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f2f2f2")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, -1), (-1, -1), NAVY),
            ]
        )
    table.setStyle(TableStyle(styles))
    return table


def _category_total_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    sections = list((report.get("excel_forecast") or {}).get("sections") or [])
    rows: list[dict[str, Any]] = []
    for section in sections:
        category = str(section.get("category") or "").strip()
        if not category or is_all_categories_label(category):
            continue
        total = section.get("total_ytd")
        if not is_finite_amount(total):
            totals = [value for value in (section.get("totals") or []) if is_finite_amount(value)]
            total = float(sum(float(value) for value in totals)) if totals else None
        rows.append({
            "category": category,
            "forecast_amount": float(total) if is_finite_amount(total) else None,
        })
    finite = [row for row in rows if is_finite_amount(row.get("forecast_amount"))]
    denom = sum(float(row["forecast_amount"]) for row in finite)
    for row in rows:
        amount = row.get("forecast_amount")
        row["percent"] = (float(amount) / denom) if is_finite_amount(amount) and denom else None
    rows.sort(key=lambda item: (-(item["forecast_amount"] or float("-inf")), item["category"]))
    return rows


def _monthly_detail_table(
    report: dict[str, Any],
    styles: dict[str, ParagraphStyle],
    width: float,
    year_summaries: list[dict[str, Any]],
) -> Table:
    month_header = [
        Paragraph("Month", styles["cellHeader"]),
        Paragraph("Forecast amount", styles["cellHeader"]),
        Paragraph("Lower expected value", styles["cellHeader"]),
        Paragraph("Upper expected value", styles["cellHeader"]),
        Paragraph("Change from previous month", styles["cellHeader"]),
    ]
    month_rows: list[list[Any]] = [month_header]
    emphasize_rows: list[int] = []
    monthly_rows = list(report.get("monthly_forecasts") or [])
    by_year: dict[int, list[dict[str, Any]]] = {}
    for row in monthly_rows:
        iso = str(row.get("month") or "")
        year = int(iso[:4]) if len(iso) >= 4 and iso[:4].isdigit() else 0
        by_year.setdefault(year, []).append(row)

    summary_by_year = {int(item["year"]): item for item in year_summaries}
    for year in sorted(year for year in by_year if year):
        for row in by_year[year]:
            month_rows.append([
                Paragraph(iso_to_label(row["month"]), styles["cell"]),
                Paragraph(_format_amount(row.get("forecast_amount")), styles["cellRight"]),
                Paragraph(_format_amount(row.get("lower_bound")), styles["cellRight"]),
                Paragraph(_format_amount(row.get("upper_bound")), styles["cellRight"]),
                Paragraph(_format_percent(row.get("change_percent")), styles["cellRight"]),
            ])
        if len(year_summaries) > 1:
            summary = summary_by_year.get(year) or {}
            label = str(summary.get("title") or f"Year {year}")
            range_label = str(summary.get("range_label") or "")
            subtotal_label = f"{label} ({range_label})" if range_label else label
            month_rows.append([
                Paragraph(subtotal_label, styles["subtotal"]),
                Paragraph(_format_amount(summary.get("total")), styles["subtotalRight"]),
                Paragraph(UNAVAILABLE, styles["cellRight"]),
                Paragraph(UNAVAILABLE, styles["cellRight"]),
                Paragraph(UNAVAILABLE, styles["cellRight"]),
            ])
            emphasize_rows.append(len(month_rows) - 1)

    finite_months = [
        float(row["forecast_amount"])
        for row in monthly_rows
        if is_finite_amount(row.get("forecast_amount"))
    ]
    month_total = float(sum(finite_months)) if finite_months else None
    if month_total is None and is_finite_amount((report.get("summary") or {}).get("overall_total")):
        month_total = float(report["summary"]["overall_total"])
    month_rows.append([
        Paragraph("Total", styles["cell"]),
        Paragraph(_format_amount(month_total), styles["cellRight"]),
        Paragraph(UNAVAILABLE, styles["cellRight"]),
        Paragraph(UNAVAILABLE, styles["cellRight"]),
        Paragraph(UNAVAILABLE, styles["cellRight"]),
    ])
    return _table(
        month_rows,
        [34 * mm, 32 * mm, 34 * mm, 34 * mm, width - 134 * mm],
        emphasize_last_row=True,
        emphasize_rows=emphasize_rows,
    )


def _footer_callback(category: str, period: str) -> Callable:
    def _draw(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7.5)
        text = f"Page {doc.page}  |  {category}  |  {period}"
        canvas.drawString(doc.leftMargin, 10 * mm, text)
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(doc.leftMargin, 13 * mm, doc.pagesize[0] - doc.rightMargin, 13 * mm)
        canvas.restoreState()

    return _draw


def render_forecast_pdf(report: dict[str, Any]) -> bytes:
    styles = _styles()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=18 * mm,
        title=report["title"],
        author="SLT-Mobitel",
    )
    width = A4[0] - 32 * mm
    story: list[Any] = []
    header = report["header"]
    summary = report["summary"]
    logo = _logo_flowable(42 * mm)
    header_row = []
    if logo is not None:
        header_row.append(logo)
    header_row.append(Paragraph(report["title"], styles["title"]))
    story.append(Table([header_row], colWidths=[46 * mm, width - 46 * mm] if logo is not None else [width]))
    story.append(Spacer(1, 6))
    meta = [
        [Paragraph("Forecast period", styles["muted"]), Paragraph(str(header["period"]), styles["body"])],
        [Paragraph("Generated date", styles["muted"]), Paragraph(str(header["generated_date"]), styles["body"])],
        [Paragraph("Category", styles["muted"]), Paragraph(str(header["category"]), styles["body"])],
        [Paragraph("Forecast months", styles["muted"]), Paragraph(str(header["forecast_month_count"]), styles["body"])],
        [Paragraph("Selected Budget Codes", styles["muted"]), Paragraph(str(header["selected_account_count"]), styles["body"])],
    ]
    story.append(KeepTogether([
        Paragraph("1. Report header", styles["section"]),
        Table(meta, colWidths=[50 * mm, width - 50 * mm]),
    ]))

    kpi_rows = [
        [Paragraph("Measure", styles["cellHeader"]), Paragraph("Value", styles["cellHeader"])],
        [Paragraph("Forecasted total", styles["cell"]), Paragraph(_format_amount(summary["overall_total"]), styles["cellRight"])],
        [Paragraph("Monthly average", styles["cell"]), Paragraph(_format_amount(summary["monthly_average"]), styles["cellRight"])],
        [
            Paragraph("Highest forecast month", styles["cell"]),
            Paragraph(
                f"{iso_to_label(summary.get('peak_month') or '')} · {_format_amount(summary['maximum_monthly_forecast'])}",
                styles["cellRight"],
            ),
        ],
        [
            Paragraph("Lowest forecast month", styles["cell"]),
            Paragraph(
                f"{iso_to_label(summary.get('trough_month') or '')} · {_format_amount(summary['minimum_monthly_forecast'])}",
                styles["cellRight"],
            ),
        ],
        [Paragraph("Historical monthly baseline", styles["cell"]), Paragraph(_format_amount(summary.get("historical_average")), styles["cellRight"])],
        [Paragraph("Difference from history", styles["cell"]), Paragraph(_format_amount(summary.get("difference")), styles["cellRight"])],
        [Paragraph("Percentage change from history", styles["cell"]), Paragraph(_format_percent(summary.get("difference_percent")), styles["cellRight"])],
        [Paragraph("Predicted direction", styles["cell"]), Paragraph(str((summary.get("forecast_trend") or {}).get("label") or "—"), styles["cellRight"])],
    ]
    story.append(KeepTogether([
        Paragraph("2. Forecast summary", styles["section"]),
        _table(kpi_rows, [width * 0.46, width * 0.54]),
    ]))

    insight_blocks = [Paragraph("3. Key financial insights", styles["section"])]
    for line in report.get("insights") or []:
        insight_blocks.append(Paragraph(f"• {line}", styles["body"]))
        insight_blocks.append(Spacer(1, 2))
    story.append(KeepTogether(insight_blocks))

    chart_months = list(report.get("chart_months") or report.get("monthly_forecasts") or [])
    year_summaries = summarize_selected_forecast_years(chart_months)

    if report.get("forecast_available", True):
        story.append(KeepTogether([
            Paragraph("4. Monthly forecast visualization", styles["section"]),
            Paragraph(
                "Monthly forecast amounts with Expected Range (90%) whiskers only where both bounds are available.",
                styles["muted"],
            ),
            Spacer(1, 4),
            MonthlyForecastBarChart(chart_months, width, 68 * mm),
        ]))

        if year_summaries:
            domain = shared_zero_inclusive_domain([item.get("total") for item in year_summaries])
            chart_width = width / max(len(year_summaries), 1)
            if len(year_summaries) == 1:
                chart_width = min(width * 0.42, 78 * mm)
            year_flowables = [
                YearTotalBarChart(summary, chart_width - 2, 62 * mm, domain=domain)
                for summary in year_summaries
            ]
            totals_block: list[Any] = [
                Paragraph("5. Yearly forecast totals", styles["section"]),
                Paragraph(
                    "Each chart sums only that calendar year’s selected forecast months. Red marks the year total.",
                    styles["muted"],
                ),
                Spacer(1, 4),
            ]
            if len(year_flowables) == 1:
                totals_block.append(year_flowables[0])
            else:
                totals_block.append(Table([year_flowables], colWidths=[chart_width] * len(year_flowables)))
            value_bits = []
            for summary in year_summaries:
                value_bits.append(f"{summary['title']}: {_format_amount(summary.get('total'))}")
            totals_block.append(Spacer(1, 3))
            totals_block.append(Paragraph(" · ".join(value_bits), styles["body"]))
            story.append(KeepTogether(totals_block))

        top_codes = list(report.get("budget_codes") or [])[:10]
        top_chart_rows = []
        for row in top_codes:
            code = str(row.get("budget_code") or "")
            name = str(row.get("account_name") or "")
            label = f"{code} · {name}" if name else code
            top_chart_rows.append({
                "label": label,
                "forecast_amount": row.get("forecast_amount"),
                "percent": row.get("percent"),
            })
        top_height = max(42 * mm, 8 * mm + len(top_chart_rows) * 7.2 * mm)
        story.append(KeepTogether([
            Paragraph("6. Top Budget Codes", styles["section"]),
            Paragraph(
                "Top available Budget Codes by forecast total for the selected period (up to 10).",
                styles["muted"],
            ),
            Spacer(1, 3),
            HorizontalRankBarChart(
                top_chart_rows,
                width,
                top_height,
                label_key="label",
                share_key="percent",
                bar_color=BLUE,
            ),
        ]))

        category_rows = _category_total_rows(report)
        show_category_chart = is_all_categories_label(header.get("category")) and len(category_rows) > 1
        if show_category_chart:
            cat_height = max(48 * mm, 8 * mm + len(category_rows) * 7.2 * mm)
            story.append(KeepTogether([
                Paragraph("7. Category forecast totals", styles["section"]),
                Paragraph(
                    "Category membership comes from the forecast result. Amounts use the same selected months as the report total.",
                    styles["muted"],
                ),
                Spacer(1, 3),
                HorizontalRankBarChart(
                    category_rows,
                    width,
                    min(cat_height, 120 * mm),
                    label_key="category",
                    share_key="percent",
                    bar_color=colors.HexColor("#6366f1"),
                ),
            ]))

        story.append(Paragraph("8. Monthly forecast details", styles["section"]))
        story.append(_monthly_detail_table(report, styles, width, year_summaries))

        code_header = [
            Paragraph("Rank", styles["cellHeader"]),
            Paragraph("Budget Code", styles["cellHeader"]),
            Paragraph("Account name", styles["cellHeader"]),
            Paragraph("Forecast total", styles["cellHeader"]),
            Paragraph("Share", styles["cellHeader"]),
        ]
        code_rows = [code_header]
        for row in report.get("budget_codes") or []:
            code_rows.append([
                Paragraph(str(row.get("rank")), styles["cell"]),
                Paragraph(str(row.get("budget_code") or ""), styles["cell"]),
                Paragraph(str(row.get("account_name") or ""), styles["cell"]),
                Paragraph(_format_amount(row.get("forecast_amount")), styles["cellRight"]),
                Paragraph(_format_share(row.get("percent")), styles["cellRight"]),
            ])
        story.append(Paragraph("9. Budget Code contribution", styles["section"]))
        story.append(_table(code_rows, [16 * mm, 28 * mm, width - 88 * mm, 28 * mm, 16 * mm]))

        comparison_header = [
            Paragraph("Forecast month", styles["cellHeader"]),
            Paragraph("Historical average", styles["cellHeader"]),
            Paragraph("Forecast amount", styles["cellHeader"]),
            Paragraph("Difference", styles["cellHeader"]),
            Paragraph("% change", styles["cellHeader"]),
            Paragraph("Historical periods used", styles["cellHeader"]),
        ]
        comparison_rows = [comparison_header]
        for row in report.get("comparison") or []:
            periods = ", ".join(
                f"{iso_to_label(item['month'])} {_format_amount(item['actual'])}"
                for item in row.get("historical_periods_used") or []
            ) or UNAVAILABLE
            # Prefer matched forecast so PDF difference/% agree with Analytics Comparison.
            matched = row.get("matched_forecast_amount")
            forecast_display = matched if matched is not None else row.get("forecast_amount")
            comparison_rows.append([
                Paragraph(iso_to_label(row["month"]), styles["cell"]),
                Paragraph(_format_amount(row.get("historical_average")), styles["cellRight"]),
                Paragraph(_format_amount(forecast_display), styles["cellRight"]),
                Paragraph(_format_amount(row.get("difference")), styles["cellRight"]),
                Paragraph(_format_percent(row.get("difference_percent")), styles["cellRight"]),
                Paragraph(periods, styles["cell"]),
            ])
        story.append(Paragraph("10. Historical vs forecast comparison", styles["section"]))
        story.append(_table(comparison_rows, [26 * mm, 28 * mm, 28 * mm, 24 * mm, 20 * mm, width - 126 * mm]))
    else:
        story.append(Paragraph(str(report.get("no_forecast_message") or "No forecast available for the selected category."), styles["section"]))

    _append_coverage_sections(story, report, styles, width)

    footer = _footer_callback(str(header.get("category") or ""), str(header.get("period") or ""))
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _append_coverage_sections(story: list[Any], report: dict[str, Any], styles: dict[str, ParagraphStyle], width: float) -> None:
    coverage_rows = [[Paragraph("Description", styles["cellHeader"]), Paragraph("Budget Codes", styles["cellHeader"])]]
    for label, count in report.get("coverage_rows") or []:
        coverage_rows.append([
            Paragraph(str(label), styles["cell"]),
            Paragraph(str(count), styles["cellRight"]),
        ])
    story.append(Paragraph("Forecast Coverage Summary", styles["section"]))
    story.append(_table(coverage_rows, [width * 0.74, width * 0.26]))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Excluded & Unavailable Budget Codes", styles["section"]))
    accounts = list(report.get("unavailable_accounts") or [])
    if not accounts:
        story.append(Paragraph(str(report.get("no_unavailable_message") or "No excluded or unavailable budget codes."), styles["body"]))
        return
    header = [
        Paragraph("ACT CODE", styles["cellHeader"]),
        Paragraph("ACT NAME", styles["cellHeader"]),
        Paragraph("CATEGORY", styles["cellHeader"]),
        Paragraph("STATUS", styles["cellHeader"]),
        Paragraph("REASON", styles["cellHeader"]),
    ]
    data = [header]
    for item in accounts:
        data.append([
            Paragraph(str(item.get("budget_code") or ""), styles["cell"]),
            Paragraph(str(item.get("account_name") or ""), styles["cell"]),
            Paragraph(str(item.get("category") or ""), styles["cell"]),
            Paragraph("UNAVAILABLE", styles["cell"]),
            Paragraph(str(item.get("reason") or ""), styles["cell"]),
        ])
    reason_width = max(width - 128 * mm, 36 * mm)
    story.append(_table(data, [22 * mm, 42 * mm, 36 * mm, 28 * mm, reason_width]))
