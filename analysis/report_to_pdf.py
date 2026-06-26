"""
report_to_pdf.py - Convert function_analysis.py's text report into a clean PDF.

function_analysis.py's terminal output is a real report (seniority,
salary, certifications, clearances, tools) but it's ASCII bar charts and
fixed-width columns - readable in a terminal, broken-looking pasted
anywhere else. This script parses that exact text format and rebuilds it
as proper tables: bordered, zebra-striped, with long names wrapping
inside their own column instead of overlapping into the next one.

Zero-count rows are kept and grayed out, not hidden - same "absence is a
finding" rule the rest of this toolkit follows.

Usage:
    python3 analysis/function_analysis.py --function program_management \\
        --no-prompt-keywords > results/program_management_report.txt

    python3 analysis/report_to_pdf.py \\
        results/program_management_report.txt \\
        results/program_management_report.pdf \\
        "Program Management — Job Function Breakdown"

Requires: pip install reportlab --break-system-packages
"""
import re
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                  TableStyle, PageBreak, KeepTogether)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER

NAVY = colors.HexColor("#1F3A5F")
LIGHT_GRAY = colors.HexColor("#F2F2F0")
MID_GRAY = colors.HexColor("#666663")
ZERO_GRAY = colors.HexColor("#AAAAA8")
ACCENT = colors.HexColor("#0F6E56")

styles = getSampleStyleSheet()
title_style = ParagraphStyle("TitleX", parent=styles["Title"], fontName="Helvetica-Bold",
                              fontSize=20, textColor=NAVY, spaceAfter=4)
subtitle_style = ParagraphStyle("SubtitleX", parent=styles["Normal"], fontName="Helvetica",
                                 fontSize=11, textColor=MID_GRAY, spaceAfter=14)
h2_style = ParagraphStyle("H2X", parent=styles["Heading2"], fontName="Helvetica-Bold",
                           fontSize=13, textColor=NAVY, spaceBefore=18, spaceAfter=4)
note_style = ParagraphStyle("NoteX", parent=styles["Normal"], fontName="Helvetica-Oblique",
                             fontSize=8.5, textColor=MID_GRAY, spaceAfter=8, leading=11)
body_style = ParagraphStyle("BodyX", parent=styles["Normal"], fontName="Helvetica",
                             fontSize=9.5, leading=13)


def parse_report(text):
    """Parse function_analysis.py's text output into structured sections."""
    lines = text.split("\n")
    data = {}

    # Header line: "Jobs matched: 1,007 / 25,046 total in dataset (4.0%)"
    m = re.search(r"Jobs matched:\s*([\d,]+)\s*/\s*([\d,]+)\s*total in dataset \(([\d.]+)%\)", text)
    data["matched"] = m.group(1) if m else "?"
    data["total"] = m.group(2) if m else "?"
    data["pct"] = m.group(3) if m else "?"

    m = re.search(r"JOB FUNCTION:\s*(\S+)", text)
    data["function_name"] = m.group(1) if m else "unknown"

    m = re.search(r"Using saved job function '[\w_]+':\s*(.+)", text)
    data["keywords"] = m.group(1).strip() if m else None

    # Split into named sections delimited by the 70-dash separator lines.
    # Real structure: block[0] = loader preamble (discarded), then alternating
    # header+subtitle blocks (odd indices) and data blocks (even indices),
    # e.g. blocks = [preamble, "HEADER\nsubtitle", "data rows...", "HEADER2\n...", "data...", ...]
    sep = "-" * 70
    blocks = text.split(sep)
    sections = []
    i = 1
    while i + 1 < len(blocks):
        header_block = blocks[i].strip()
        body = blocks[i + 1]
        header_lines = header_block.split("\n")
        header = header_lines[0].strip() if header_lines else ""
        subtitle = "\n".join(h.strip() for h in header_lines[1:] if not h.strip().startswith("Bar scale")).strip()
        sections.append((header, subtitle, body))
        i += 2
    data["sections"] = sections
    return data


def parse_seniority(body):
    rows = []
    for line in body.split("\n"):
        m = re.match(r"\s+(\w+)\s+(\d+)\s+#*\s+([\d.]+)%", line)
        if m:
            rows.append((m.group(1), m.group(2), f"{m.group(3)}%"))
    return rows


def parse_salary(body):
    rows = []
    for line in body.split("\n"):
        m = re.match(r"\s+(\w+)\s+avg \$\s*([\d,]+)\s+median \$\s*([\d,]+)\s+\((\d+)/(\d+) jobs, (\d+)% had salary data\)", line)
        if m:
            level, avg, median, n, total, cov = m.groups()
            rows.append((level, f"${avg}", f"${median}", f"{n}/{total} ({cov}%)"))
    return rows


def parse_companies(body):
    rows = []
    for line in body.split("\n"):
        m = re.match(r"\s+(.+?)\s{2,}(\d+)\s*$", line)
        if m:
            rows.append((m.group(1).strip(), m.group(2)))
    return rows


def parse_keyword_list(body):
    """Parses certifications/clearances/tools sections: name, count, pct, [companies]."""
    rows = []
    for line in body.split("\n"):
        # zero-count: "    Name    0   0.0%   [not found]"
        m = re.match(r"\s+(.+?)\s{2,}(\d+)\s+#*\s*([\d.]+)%\s+\[(.+)\]", line)
        if m:
            name, count, pct, companies = m.groups()
            rows.append((name.strip(), count, f"{pct}%", companies.strip()))
    return rows


def parse_onet(body):
    rows = []
    for line in body.split("\n"):
        m = re.match(r"\s+(.+?)\s{2,}(\d+)\s+#*\s*([\d.]+)%\s+\(avg strength ([\d.]+)\)\s+\[(.+)\]", line)
        if m:
            name, count, pct, strength, companies = m.groups()
            rows.append((name.strip(), count, f"{pct}%", strength, companies.strip()))
    return rows


def make_simple_table(rows, col_widths, header=None, zebra=True):
    data = ([header] if header else []) + rows
    t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
    ]
    if header:
        style += [
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ]
    if zebra:
        start = 1 if header else 0
        for i in range(start, len(data)):
            if (i - start) % 2 == 1:
                style.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GRAY))
    t.setStyle(TableStyle(style))
    return t


cell_style = ParagraphStyle("CellX", parent=styles["Normal"], fontName="Helvetica",
                             fontSize=8, leading=10)
cell_style_bold = ParagraphStyle("CellBoldX", parent=cell_style, fontName="Helvetica-Bold")


def make_keyword_table(rows, col_widths, header):
    """Keyword/cert/tool tables: gray out zero-count rows. Long names wrap via Paragraph."""
    header_cells = [Paragraph(f"<b>{h}</b>", ParagraphStyle("HdrX", parent=cell_style, textColor=colors.white)) for h in header]
    data = [header_cells]
    for row in rows:
        is_zero = str(row[1]).strip() == "0"
        color = ZERO_GRAY if is_zero else colors.black
        wrapped = [Paragraph(str(cell), ParagraphStyle("CX", parent=cell_style, textColor=color)) for cell in row]
        data.append(wrapped)

    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for i, row in enumerate(rows, start=1):
        is_zero = str(row[1]).strip() == "0"
        if not is_zero and (i - 1) % 2 == 1:
            style.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GRAY))
    t.setStyle(TableStyle(style))
    return t


SECTION_RENDERERS = {
    "SENIORITY BREAKDOWN": ("seniority", parse_seniority, ["Level", "Jobs", "% of matched"], [2.2*inch, 1.2*inch, 1.5*inch]),
    "AVG SALARY BY SENIORITY": ("salary", parse_salary, ["Level", "Avg", "Median", "Coverage"], [1.6*inch, 1.4*inch, 1.4*inch, 1.6*inch]),
    "TOP COMPANIES": ("companies", parse_companies, ["Company", "Jobs"], [4*inch, 2*inch]),
    "CERTIFICATIONS": ("keyword", parse_keyword_list, ["Certification", "Count", "%", "Top companies"], [2.6*inch, 0.5*inch, 0.5*inch, 2.4*inch]),
    "AEROSPACE COMPLIANCE": ("keyword", parse_keyword_list, ["Standard / Credential", "Count", "%", "Top companies"], [2.6*inch, 0.5*inch, 0.5*inch, 2.4*inch]),
    "SECURITY CLEARANCES": ("keyword", parse_keyword_list, ["Clearance", "Count", "%", "Top companies"], [2.6*inch, 0.5*inch, 0.5*inch, 2.4*inch]),
    "AI / DATA ENGINEERING TOOLS": ("keyword", parse_keyword_list, ["Tool", "Count", "%", "Top companies"], [2.6*inch, 0.5*inch, 0.5*inch, 2.4*inch]),
    "PM / DESIGN / COLLABORATION": ("keyword", parse_keyword_list, ["Tool / Framework", "Count", "%", "Top companies"], [2.6*inch, 0.5*inch, 0.5*inch, 2.4*inch]),
    "AV / AUTONOMOUS VEHICLE": ("keyword", parse_keyword_list, ["Standard / Tool", "Count", "%", "Top companies"], [2.6*inch, 0.5*inch, 0.5*inch, 2.4*inch]),
    "AEROSPACE-NATIVE ENGINEERING": ("keyword", parse_keyword_list, ["Platform", "Count", "%", "Top companies"], [2.6*inch, 0.5*inch, 0.5*inch, 2.4*inch]),
    "AI INFRASTRUCTURE HARDWARE": ("keyword", parse_keyword_list, ["Hardware", "Count", "%", "Top companies"], [2.6*inch, 0.5*inch, 0.5*inch, 2.4*inch]),
    "OTHER TOOLS & SOFTWARE": ("onet", parse_onet, ["Tool (O*NET)", "Count", "%", "Strength", "Top companies"], [2.4*inch, 0.5*inch, 0.5*inch, 0.6*inch, 2.0*inch]),
}


def build_pdf(report_path, out_path, display_title):
    with open(report_path, "r") as f:
        text = f.read()

    data = parse_report(text)
    doc = SimpleDocTemplate(out_path, pagesize=letter,
                             topMargin=0.6*inch, bottomMargin=0.6*inch,
                             leftMargin=0.55*inch, rightMargin=0.55*inch)
    story = []

    story.append(Paragraph(display_title, title_style))
    story.append(Paragraph(
        f"{data['matched']} of {data['total']} postings matched ({data['pct']}% of the dataset) "
        f"&middot; Aerospace AI Job Analysis &middot; github.com/mtoyserkani/aerospace-ai-job-analysis",
        subtitle_style))

    if data.get("keywords"):
        story.append(Paragraph(f"<b>Matched against:</b> {data['keywords']}", note_style))
        story.append(Spacer(1, 6))

    for header, subtitle, body in data["sections"]:
        if not header or header.startswith("="):
            continue
        # find a matching renderer by prefix match
        kind = None
        for key, val in SECTION_RENDERERS.items():
            if header.startswith(key):
                kind = val
                break
        if kind is None:
            continue
        renderer_type, parser_fn, col_header, col_widths = kind
        rows = parser_fn(body)
        if not rows:
            continue

        block = [Paragraph(header, h2_style)]
        if subtitle:
            block.append(Paragraph(subtitle.replace("\n", " "), note_style))

        if renderer_type == "keyword" or renderer_type == "onet":
            table = make_keyword_table(rows, col_widths, col_header)
        else:
            table = make_simple_table(rows, col_widths, header=col_header)
        block.append(table)
        block.append(Spacer(1, 4))

        # Keep header+table together only for short tables; long keyword tables
        # are allowed to break across pages naturally (KeepTogether would force
        # an entire 60-row table onto one page, leaving huge gaps).
        if renderer_type in ("seniority", "salary", "companies"):
            story.append(KeepTogether(block))
        else:
            story.extend(block)

    doc.build(story)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python3 analysis/report_to_pdf.py <report.txt> <output.pdf> \"<Display Title>\"")
        print()
        print("Example:")
        print('  python3 analysis/function_analysis.py --function program_management \\')
        print("      --no-prompt-keywords > results/program_management_report.txt")
        print("  python3 analysis/report_to_pdf.py \\")
        print("      results/program_management_report.txt \\")
        print("      results/program_management_report.pdf \\")
        print('      "Program Management — Job Function Breakdown"')
        sys.exit(1)

    report_path, out_path, display_title = sys.argv[1], sys.argv[2], sys.argv[3]
    build_pdf(report_path, out_path, display_title)
    print(f"Written: {out_path}")
