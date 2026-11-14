"""Build the engineering report PDF (report/ETL_Engineering_Report.pdf) from smart_factory.db."""
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle, KeepTogether)

HERE = Path(__file__).resolve().parent
DB = HERE.parent / "smart_factory.db"
FIG = HERE / "figures"
FIG.mkdir(exist_ok=True)
OUT = HERE / "ETL_Engineering_Report.pdf"


# ------------------------------------------------------------------ diagram
def draw_architecture(path):
    fig, ax = plt.subplots(figsize=(11.5, 4.6), dpi=200)
    ax.set_xlim(0, 124); ax.set_ylim(0, 46); ax.axis("off")

    def box(x, y, w, h, title, body, fc, tsize=9):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2",
                                    fc=fc, ec="#33415c", lw=1.1))
        ax.text(x + w / 2, y + h - 2.0, title, ha="center", va="top", fontsize=tsize, weight="bold")
        ax.text(x + w / 2, y + h - 6.0, body, ha="center", va="top", fontsize=7.3, linespacing=1.35)

    def arrow(x1, y1, x2, y2):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="-|>", lw=1.2, color="#33415c"))

    for x, label in [(11, "SOURCES"), (36.5, "EXTRACT"), (68, "TRANSFORM"), (111, "LOAD")]:
        ax.text(x, 44.5, label, ha="center", fontsize=9, color="#5c677d", weight="bold")

    box(1, 25, 20, 15, "iot_telemetry_data.csv", "UTF-8\n10,000 rows\n7 columns", "#e3f2fd", 8.3)
    box(1, 3, 20, 15, "qc_inspection_logs.csv", "ISO-8859-1\n2,500 rows\n6 columns", "#fff3e0", 8.3)
    box(25, 25, 23, 15, "extract_sensor_data()", "parse timestamps\nkeep ts >= max(ts) - 7 days\n-> 6,971 rows", "#e8eaf6", 8.5)
    box(25, 3, 23, 15, "extract_qc_data()", "read with ISO-8859-1\nkeep STATUS == 'Completed'\n-> 1,463 rows", "#e8eaf6", 8.5)
    box(52, 25, 24, 15, "clean + standardize", "snake_case columns\nerror codes / bounds -> NaN\nffill per machine\ndata_quality, record_id", "#e8f5e9", 8.5)
    box(52, 3, 24, 15, "standardize QC", "INSPECTION_TIME -> timestamp\nLINE_CODE -> line_id\nEQUIPMENT_TAG -> machine_id", "#e8f5e9", 8.5)
    box(80, 21, 18, 12, "LEFT JOIN", "on (timestamp,\nline_id, machine_id)\n-> quality_status", "#e8f5e9", 8.5)
    box(80, 6.5, 18, 10, "aggregate_hourly()", "group by (hour,\nline_id, machine_id)", "#e8f5e9", 8.3)

    ax.add_patch(FancyBboxPatch((101, 1.5), 21, 38.5, boxstyle="round,pad=0.4,rounding_size=1.2",
                                fc="#fce4ec", ec="#33415c", lw=1.1))
    ax.text(111.5, 38.8, "smart_factory.db", ha="center", va="top", fontsize=8.8, weight="bold")
    tables = {"sensor_readings": (27, "6,971"), "hourly_summary": (15, "3,801"), "quality_checks": (3, "1,463")}
    for t, (y, n) in tables.items():
        ax.add_patch(FancyBboxPatch((103, y), 17, 7, boxstyle="round,pad=0.2", fc="white", ec="#ad1457", lw=0.8))
        ax.text(111.5, y + 4.6, t, ha="center", fontsize=7.5, weight="bold")
        ax.text(111.5, y + 1.6, f"{n} rows", ha="center", fontsize=7)

    arrow(21.4, 32.5, 24.6, 32.5); arrow(21.4, 10.5, 24.6, 10.5)
    arrow(48.4, 32.5, 51.6, 32.5); arrow(48.4, 10.5, 51.6, 10.5)
    arrow(76.4, 30, 79.6, 29); arrow(76.4, 13.5, 79.6, 24)      # into join
    arrow(89, 20.6, 89, 16.9)                                   # join -> aggregate
    arrow(76.4, 37, 102.6, 31)                                  # -> sensor_readings
    arrow(76.4, 5, 102.6, 6.5)                                  # -> quality_checks
    arrow(98.4, 11.5, 102.6, 18)                                # -> hourly_summary
    fig.savefig(path, bbox_inches="tight"); plt.close(fig)


# ------------------------------------------------------------------ query "screenshots"
def query_figure(conn, sql, path, title, max_col_chars=19):
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    fmt = lambda v: "NULL" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))[:max_col_chars]
    cells = [[fmt(v) for v in r] for r in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(cols)]
    line = lambda vals: " | ".join(v.ljust(w) for v, w in zip(vals, widths))
    sep = "-+-".join("-" * w for w in widths)
    sql_lines = ["sqlite> " + sql.strip().splitlines()[0]] + \
                ["   ...> " + l for l in sql.strip().splitlines()[1:]]
    text = "\n".join(sql_lines + [line(cols), sep] + [line(r) for r in cells] +
                     [f"({len(rows)} rows)"])
    n_lines = text.count("\n") + 1
    width_chars = max(len(l) for l in text.splitlines())
    fig = plt.figure(figsize=(max(6, width_chars * 0.075), 0.2 * n_lines + 0.45), dpi=200)
    fig.patch.set_facecolor("#1e1e1e")
    fig.text(0.012, 1 - 0.12 / (0.2 * n_lines + 0.45), title, color="#9cdcfe", fontsize=8,
             family="monospace", va="top")
    fig.text(0.012, 1 - 0.38 / (0.2 * n_lines + 0.45), text, color="#d4d4d4", fontsize=7.6,
             family="monospace", va="top", linespacing=1.32)
    fig.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.12); plt.close(fig)


QUERIES = [
    ("q1_counts.png", "smart_factory.db - table row counts",
     """SELECT 'sensor_readings' AS table_name, COUNT(*) AS n FROM sensor_readings
UNION ALL SELECT 'quality_checks', COUNT(*) FROM quality_checks
UNION ALL SELECT 'hourly_summary', COUNT(*) FROM hourly_summary;"""),
    ("q2_sensor.png", "sensor_readings - sample rows",
     """SELECT record_id, timestamp, line_id AS line, machine_id AS mch,
       temperature AS temp, pressure AS pres, vibration AS vib, power, data_quality
FROM sensor_readings WHERE data_quality = 'estimated' AND temperature IS NOT NULL LIMIT 5;"""),
    ("q3_qc.png", "quality_checks - sample rows",
     """SELECT * FROM quality_checks WHERE qc_result = 'FAIL' LIMIT 5;"""),
    ("q4_hourly.png", "hourly_summary - sample rows",
     """SELECT summary_id AS id, hour, line_id AS line, machine_id AS mch,
       avg_temperature AS avg_t, min_temperature AS min_t, max_temperature AS max_t,
       avg_pressure AS avg_p, avg_vibration AS avg_v,
       total_checks AS checks, defect_count AS fails, defect_rate AS rate
FROM hourly_summary WHERE total_checks > 1 LIMIT 5;"""),
    ("q5_analysis.png", "Analytical query - defect rate per production line",
     """SELECT line_id, SUM(total_checks) AS checks, SUM(defect_count) AS fails,
       ROUND(100.0 * SUM(defect_count) / SUM(total_checks), 2) AS defect_rate_pct,
       ROUND(AVG(avg_temperature), 2) AS avg_temp
FROM hourly_summary GROUP BY line_id ORDER BY defect_rate_pct DESC;"""),
]


# ------------------------------------------------------------------ PDF
def build():
    draw_architecture(FIG / "architecture.png")
    conn = sqlite3.connect(DB)
    for fname, title, sql in QUERIES:
        query_figure(conn, sql, FIG / fname, title)
    stats = dict(
        estimated=conn.execute("SELECT COUNT(*) FROM sensor_readings WHERE data_quality='estimated'").fetchone()[0],
        nulls=conn.execute("SELECT COUNT(*) FROM sensor_readings WHERE temperature IS NULL OR pressure IS NULL OR vibration IS NULL").fetchone()[0],
    )
    conn.close()

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=17, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=ss["Normal"], fontSize=9, textColor=colors.HexColor("#5c677d"),
                         alignment=1, spaceAfter=8)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, spaceBefore=8, spaceAfter=4,
                        textColor=colors.HexColor("#1f3a68"))
    body = ParagraphStyle("b", parent=ss["Normal"], fontSize=9, leading=12.2, spaceAfter=4)
    cap = ParagraphStyle("c", parent=body, fontSize=7.8, textColor=colors.HexColor("#5c677d"), alignment=1)
    bullet = ParagraphStyle("bl", parent=body, leftIndent=12, bulletIndent=3, spaceAfter=2)

    def img(name, width_cm):
        from reportlab.lib.utils import ImageReader
        w, h = ImageReader(str(FIG / name)).getSize()
        return Image(str(FIG / name), width=width_cm * cm, height=width_cm * cm * h / w)

    def bullets(items):
        return [Paragraph(t, bullet, bulletText="•") for t in items]

    tbl_style = TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a68")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b0b8c8")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5fa")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)])

    s = []
    s.append(Paragraph("Smart Factory ETL Pipeline - Engineering Report", h1))
    s.append(Paragraph("Deliverables: etl_pipeline.py &middot; smart_factory.db &middot; this report", sub))

    s.append(Paragraph("1. System architecture &amp; data flow", h2))
    s.append(Paragraph(
        "The pipeline is a single Python module (<b>etl_pipeline.py</b>, pandas + sqlite3) organised as "
        "one function per step, orchestrated by <font face='Courier'>run_pipeline()</font>. Each stage "
        "raises a typed <font face='Courier'>ETLError</font> on failure (missing file, decoding error, "
        "malformed CSV, missing column, SQLite error); <font face='Courier'>main()</font> logs it and exits "
        "with status 1. The load is executed inside one transaction, so a failure never leaves a half-written "
        "database, and the script is idempotent: every run drops and rebuilds the three tables.", body))
    s.append(img("architecture.png", 17.5))
    s.append(Paragraph("Figure 1 - Data flow from the two CSV sources to the three SQLite tables "
                       "(row counts from the actual run).", cap))

    s.append(Paragraph("2. Extraction", h2))
    s += bullets([
        "<b>Telemetry</b> (UTF-8): timestamps are parsed and the latest one (2026-03-11 00:00) is the "
        "reference; rows with <font face='Courier'>timestamp &gt;= max - 7 days</font> are kept "
        "(10,000 &rarr; 6,971). A source-row number is attached for lineage.",
        "<b>QC log</b>: read with <font face='Courier'>encoding='ISO-8859-1'</font>; only "
        "<font face='Courier'>INSPECTION_STATUS == 'Completed'</font> rows are kept "
        "(2,500 &rarr; 1,463; Pending and Cancelled are discarded).",
    ])

    s.append(Paragraph("3. Data cleaning logic", h2))
    s.append(Paragraph(
        "Columns are normalised to snake_case (<font face='Courier'>Line ID &rarr; line_id</font>) and "
        "mapped to standard names (temperature, pressure, vibration, power). Cleaning is then applied to the "
        "three measured signals:", body))
    t = Table([
        ["Signal", "Valid range", "Error codes -> NaN", "Out of range", "Repaired by"],
        ["temperature", "0 - 150 deg C", "278 (-999.0)", "0", "ffill"],
        ["pressure", "0 - 10 bar", "199 (-1.0)", "57", "ffill"],
        ["vibration", "0 - 100 mm/s", "0", "152", "ffill"],
    ], colWidths=[2.6 * cm, 3 * cm, 3.4 * cm, 2.6 * cm, 2.6 * cm], style=tbl_style)
    s.append(t); s.append(Spacer(1, 5))
    s += bullets([
        "Error codes (-999.0, -1.0) and nulls become NaN; values outside the operating bounds are also "
        "set to NaN. Both error codes lie outside the valid ranges, so they are treated the same way.",
        "Rows are sorted by time <i>within each (line_id, machine_id)</i> and invalid values are "
        "forward-filled with <font face='Courier'>groupby().ffill()</font>, so a machine never inherits "
        "another machine's reading.",
        f"<font face='Courier'>data_quality</font> = 'estimated' when any signal in the row was filled "
        f"({stats['estimated']} rows), otherwise 'good'. {stats['nulls']} values remain NULL because the "
        "invalid reading was the first one for that machine in the window (nothing to fill from).",
        "<font face='Courier'>record_id</font> = first 16 hex characters of SHA-1(timestamp | line_id | "
        "machine_id | source row). The source row is included because the raw file contains 556 repeated "
        "(timestamp, line, machine) readings, which would otherwise produce primary-key collisions.",
    ])

    s.append(Paragraph("4. Join &amp; aggregation logic", h2))
    s += bullets([
        "QC columns are renamed to the telemetry schema (INSPECTION_TIME &rarr; timestamp, LINE_CODE &rarr; "
        "line_id, EQUIPMENT_TAG &rarr; machine_id); the literal 'None' defect type is stored as NULL.",
        "<b>Left join</b> sensor &larr; QC on the composite key (timestamp, line_id, machine_id). Seven "
        "completed QC keys appear twice; to guarantee one output row per sensor reading, the join side is "
        "de-duplicated with <b>FAIL taking precedence over PASS</b> (conservative). The join is checked with "
        "<font face='Courier'>validate='many_to_one'</font> and a row-count assertion.",
        "<font face='Courier'>quality_status</font> = qc_result (PASS/FAIL), or 'NOT_CHECKED' when no QC "
        "record matches. Result: 908 PASS, 210 FAIL, 5,853 NOT_CHECKED.",
        "Hourly aggregation groups by (timestamp floored to the hour, line_id, machine_id) and computes "
        "avg/min/max of temperature, pressure and vibration, total_checks (PASS + FAIL), defect_count (FAIL) "
        "and defect_rate = defect_count / total_checks &times; 100. Hours with no check get "
        "defect_rate = NULL rather than a misleading 0. The table keeps all the columns the specification "
        "names and also stores min/max pressure and vibration.",
        "<b>quality_checks</b> stores every completed inspection (1,463 rows, including the duplicates) so "
        "no QC evidence is lost; de-duplication only affects the join.",
    ])

    s.append(Paragraph("5. Evidence: loaded tables &amp; sample queries", h2))
    s.append(Paragraph("Output of queries executed against the generated <b>smart_factory.db</b>.", body))
    from reportlab.lib.utils import ImageReader
    for fname, title, _ in QUERIES:
        w_px, _h = ImageReader(str(FIG / fname)).getSize()
        s.append(KeepTogether([img(fname, min(17.5, w_px / 200 * 2.54)), Spacer(1, 6)]))

    s.append(Paragraph("6. Validation", h2))
    s += bullets([
        "No stored reading violates the operating bounds (query returns 0 rows).",
        "Recomputing defect_count / total_checks &times; 100 matches defect_rate for every hourly row.",
        "SUM(total_checks) = 1,118 = PASS + FAIL rows of the join; SUM(defect_count) = 210 = FAIL rows.",
        "Sensor window: 2026-03-04 00:00 to 2026-03-11 00:00 (exactly 7 days back from the latest reading).",
    ])

    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=1.7 * cm, rightMargin=1.7 * cm,
                            topMargin=1.4 * cm, bottomMargin=1.4 * cm,
                            title="Smart Factory ETL - Engineering Report")
    doc.build(s, onFirstPage=_footer, onLaterPages=_footer)
    print("wrote", OUT)


def _footer(canvas, doc):
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#8d99ae"))
    canvas.drawRightString(A4[0] - 1.7 * cm, 0.8 * cm, f"Page {doc.page}")


if __name__ == "__main__":
    build()
