"""Professional PDF for a persisted forecast report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
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
from reportlab.lib.utils import ImageReader

from app.services.report_builder import UNAVAILABLE, iso_to_label

NAVY = colors.HexColor("#071a33")
BLUE = colors.HexColor("#1f7ae0")
LINE = colors.HexColor("#dbe4ee")
MUTED = colors.HexColor("#66788a")
LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "slt-mobitel-logo.svg"


class ForecastLineChart(Flowable):
    def __init__(self, rows: list[dict[str, Any]], width: float, height: float) -> None:
        super().__init__()
        self.rows = rows
        self.width = width
        self.height = height

    def draw(self) -> None:
        points = []
        for row in self.rows:
            try:
                amount = float(row.get("forecast_amount"))
            except (TypeError, ValueError):
                continue
            lower = row.get("lower_bound")
            upper = row.get("upper_bound")
            try:
                lower_value = float(lower)
                upper_value = float(upper)
                if lower_value != lower_value or upper_value != upper_value:
                    raise ValueError
            except (TypeError, ValueError):
                lower_value = None
                upper_value = None
            points.append((iso_to_label(str(row.get("month") or "")), amount, lower_value, upper_value))
        if not points:
            return
        canvas = self.canv
        left, right, bottom, top = 36, self.width - 8, 28, self.height - 10
        values = [amount for _, amount, _, _ in points]
        values.extend(value for _, _, lower, upper in points for value in (lower, upper) if value is not None)
        low = min(values)
        high = max(values)
        span = high - low
        padding = max(abs(high) * 0.08, 1) if span < 1e-9 else span * 0.08
        low -= padding
        high += padding
        span = high - low or 1
        canvas.setStrokeColor(LINE)
        canvas.setFillColor(colors.white)
        canvas.rect(left, bottom, right - left, top - bottom, stroke=1, fill=1)
        coords = []
        count = max(len(points) - 1, 1)
        for index, (label, amount, lower, upper) in enumerate(points):
            x = left + ((right - left) * index / count)
            y = bottom + ((amount - low) / span) * (top - bottom)
            coords.append((x, y, label, lower, upper))
            if lower is not None and upper is not None:
                y1 = bottom + ((lower - low) / span) * (top - bottom)
                y2 = bottom + ((upper - low) / span) * (top - bottom)
                canvas.setFillColor(colors.Color(0.12, 0.48, 0.88, alpha=0.16))
                canvas.rect(x - 4, min(y1, y2), 8, abs(y2 - y1), stroke=0, fill=1)
        canvas.setStrokeColor(BLUE)
        canvas.setLineWidth(2)
        for index in range(1, len(coords)):
            canvas.line(coords[index - 1][0], coords[index - 1][1], coords[index][0], coords[index][1])
        canvas.setFillColor(BLUE)
        for x, y, label, _, _ in coords:
            canvas.circle(x, y, 2.4, stroke=0, fill=1)
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 6)
            canvas.drawCentredString(x, bottom - 12, label)
            canvas.setFillColor(BLUE)
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(4, top - 2, "LKR Mn")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "section": ParagraphStyle(
            "ReportSection",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
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
        "cellRight": ParagraphStyle(
            "ReportCellRight",
            parent=base["Normal"],
            fontName="Helvetica",
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
        image = Image(ImageReader(__import__("io").BytesIO(png)))
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


def _table(data: list[list[Any]], col_widths: list[float]) -> Table:
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle([
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
        ])
    )
    return table


def render_forecast_pdf(report: dict[str, Any]) -> bytes:
    from io import BytesIO

    styles = _styles()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
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
        [Paragraph("Measure", styles["cell"]), Paragraph("Value", styles["cell"])],
        [Paragraph("Forecasted total", styles["cell"]), Paragraph(_format_amount(summary["overall_total"]), styles["cellRight"])],
        [Paragraph("Monthly average", styles["cell"]), Paragraph(_format_amount(summary["monthly_average"]), styles["cellRight"])],
        [
            Paragraph("Highest forecast month", styles["cell"]),
            Paragraph(f"{iso_to_label(summary.get('peak_month') or '')} · {_format_amount(summary['maximum_monthly_forecast'])}", styles["cellRight"]),
        ],
        [
            Paragraph("Lowest forecast month", styles["cell"]),
            Paragraph(f"{iso_to_label(summary.get('trough_month') or '')} · {_format_amount(summary['minimum_monthly_forecast'])}", styles["cellRight"]),
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

    story.append(KeepTogether([
        Paragraph("4. Monthly forecast visualization", styles["section"]),
        Paragraph("Forecast line with Expected Range (90%) only where both bounds are available.", styles["muted"]),
        Spacer(1, 4),
        ForecastLineChart(report.get("chart_months") or [], width, 58 * mm),
    ]))

    code_header = [
        Paragraph("Rank", styles["cell"]),
        Paragraph("Budget Code", styles["cell"]),
        Paragraph("Account name", styles["cell"]),
        Paragraph("Forecast total", styles["cell"]),
        Paragraph("Share", styles["cell"]),
    ]
    code_rows = [code_header]
    for row in report.get("budget_codes") or []:
        code_rows.append([
            Paragraph(str(row.get("rank")), styles["cell"]),
            Paragraph(str(row.get("budget_code") or ""), styles["cell"]),
            Paragraph(str(row.get("account_name") or ""), styles["cell"]),
            Paragraph(_format_amount(row.get("forecast_amount")), styles["cellRight"]),
            Paragraph(_format_percent((row.get("percent") or 0) * 100), styles["cellRight"]),
        ])
    story.append(KeepTogether([
        Paragraph("5. Budget Code contribution", styles["section"]),
        _table(code_rows, [16 * mm, 28 * mm, width - 88 * mm, 28 * mm, 16 * mm]),
    ]))

    month_header = [
        Paragraph("Month", styles["cell"]),
        Paragraph("Forecast amount", styles["cell"]),
        Paragraph("Lower expected value", styles["cell"]),
        Paragraph("Upper expected value", styles["cell"]),
        Paragraph("Change from previous month", styles["cell"]),
    ]
    month_rows = [month_header]
    for row in report.get("monthly_forecasts") or []:
        month_rows.append([
            Paragraph(iso_to_label(row["month"]), styles["cell"]),
            Paragraph(_format_amount(row.get("forecast_amount")), styles["cellRight"]),
            Paragraph(_format_amount(row.get("lower_bound")), styles["cellRight"]),
            Paragraph(_format_amount(row.get("upper_bound")), styles["cellRight"]),
            Paragraph(_format_percent(row.get("change_percent")), styles["cellRight"]),
        ])
    story.append(KeepTogether([Paragraph("6. Monthly forecast table", styles["section"])]))
    story.append(_table(month_rows, [24 * mm, 32 * mm, 36 * mm, 36 * mm, width - 128 * mm]))

    comparison_header = [
        Paragraph("Forecast month", styles["cell"]),
        Paragraph("Historical average", styles["cell"]),
        Paragraph("Forecast amount", styles["cell"]),
        Paragraph("Difference", styles["cell"]),
        Paragraph("% change", styles["cell"]),
        Paragraph("Historical periods used", styles["cell"]),
    ]
    comparison_rows = [comparison_header]
    for row in report.get("comparison") or []:
        periods = ", ".join(
            f"{iso_to_label(item['month'])} {_format_amount(item['actual'])}"
            for item in row.get("historical_periods_used") or []
        ) or UNAVAILABLE
        comparison_rows.append([
            Paragraph(iso_to_label(row["month"]), styles["cell"]),
            Paragraph(_format_amount(row.get("historical_average")), styles["cellRight"]),
            Paragraph(_format_amount(row.get("forecast_amount")), styles["cellRight"]),
            Paragraph(_format_amount(row.get("difference")), styles["cellRight"]),
            Paragraph(_format_percent(row.get("difference_percent")), styles["cellRight"]),
            Paragraph(periods, styles["cell"]),
        ])
    story.append(KeepTogether([Paragraph("7. Historical vs forecast comparison", styles["section"])]))
    story.append(_table(comparison_rows, [26 * mm, 28 * mm, 28 * mm, 24 * mm, 20 * mm, width - 126 * mm]))

    document.build(story)
    return buffer.getvalue()
