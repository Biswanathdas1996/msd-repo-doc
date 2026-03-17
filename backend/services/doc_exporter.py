import io
import re
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from fpdf import FPDF


def _strip_markdown(text: str) -> list[dict]:
    lines = text.split("\n")
    blocks = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            blocks.append({"type": "blank", "text": ""})
            continue

        if stripped.startswith("######"):
            blocks.append({"type": "h6", "text": stripped[6:].strip()})
        elif stripped.startswith("#####"):
            blocks.append({"type": "h5", "text": stripped[5:].strip()})
        elif stripped.startswith("####"):
            blocks.append({"type": "h4", "text": stripped[4:].strip()})
        elif stripped.startswith("###"):
            blocks.append({"type": "h3", "text": stripped[3:].strip()})
        elif stripped.startswith("##"):
            blocks.append({"type": "h2", "text": stripped[2:].strip()})
        elif stripped.startswith("#"):
            blocks.append({"type": "h1", "text": stripped[1:].strip()})
        elif stripped.startswith("- ") or stripped.startswith("* "):
            blocks.append({"type": "bullet", "text": stripped[2:].strip()})
        elif re.match(r"^\d+\.\s", stripped):
            blocks.append({"type": "numbered", "text": re.sub(r"^\d+\.\s", "", stripped)})
        elif stripped.startswith("|") and stripped.endswith("|"):
            blocks.append({"type": "table_row", "text": stripped})
        elif stripped.startswith("---") or stripped.startswith("***"):
            blocks.append({"type": "hr", "text": ""})
        else:
            blocks.append({"type": "paragraph", "text": stripped})
    return blocks


def _clean_inline_md(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    return text


def _sanitize_for_pdf(text: str) -> str:
    replacements = {
        "\u2022": "-",
        "\u2013": "-",
        "\u2014": "--",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u00a0": " ",
        "\u2192": "->",
        "\u2190": "<-",
        "\u2193": "v",
        "\u2191": "^",
    }
    for char, repl in replacements.items():
        text = text.replace(char, repl)
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        text = text.encode("latin-1", errors="replace").decode("latin-1")
    return text


def export_to_docx(sections: list[dict], solution_name: str) -> bytes:
    doc = Document()

    style = doc.styles["Normal"]
    font = style.font
    font.name = "Calibri"
    font.size = Pt(11)
    font.color.rgb = RGBColor(0x33, 0x33, 0x33)

    title = doc.add_heading(f"{solution_name} — Documentation", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph("")

    for section in sorted(sections, key=lambda s: s.get("order", 0)):
        content = section.get("content", "")
        blocks = _strip_markdown(content)
        table_rows = []

        for block in blocks:
            btype = block["type"]
            text = _clean_inline_md(block["text"])

            if btype == "blank":
                if table_rows:
                    _add_table_to_doc(doc, table_rows)
                    table_rows = []
                continue
            elif btype == "table_row":
                table_rows.append(text)
                continue
            else:
                if table_rows:
                    _add_table_to_doc(doc, table_rows)
                    table_rows = []

            if btype == "h1":
                doc.add_heading(text, level=1)
            elif btype == "h2":
                doc.add_heading(text, level=2)
            elif btype == "h3":
                doc.add_heading(text, level=3)
            elif btype in ("h4", "h5", "h6"):
                p = doc.add_paragraph()
                run = p.add_run(text)
                run.bold = True
                run.font.size = Pt(12)
            elif btype == "bullet":
                doc.add_paragraph(text, style="List Bullet")
            elif btype == "numbered":
                doc.add_paragraph(text, style="List Number")
            elif btype == "hr":
                p = doc.add_paragraph()
                p.add_run("─" * 60).font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)
            else:
                doc.add_paragraph(text)

        if table_rows:
            _add_table_to_doc(doc, table_rows)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _add_table_to_doc(doc, rows: list[str]):
    parsed = []
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if all(re.match(r"^[-:]+$", c) for c in cells):
            continue
        parsed.append(cells)

    if len(parsed) < 1:
        return

    cols = len(parsed[0])
    table = doc.add_table(rows=len(parsed), cols=cols)
    table.style = "Light Grid Accent 1"

    for i, row_data in enumerate(parsed):
        for j, cell_text in enumerate(row_data):
            if j < cols:
                table.rows[i].cells[j].text = _clean_inline_md(cell_text)

    doc.add_paragraph("")


class PDFDoc(FPDF):
    def __init__(self, solution_name: str):
        super().__init__()
        self.solution_name = solution_name
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, _sanitize_for_pdf(self.solution_name), align="L")
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def export_to_pdf(sections: list[dict], solution_name: str) -> bytes:
    pdf = PDFDoc(solution_name)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 15, _sanitize_for_pdf(solution_name), ln=True, align="C")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 10, "Generated Documentation", ln=True, align="C")
    pdf.ln(10)

    for section in sorted(sections, key=lambda s: s.get("order", 0)):
        content = section.get("content", "")
        blocks = _strip_markdown(content)
        table_rows = []

        for block in blocks:
            btype = block["type"]
            text = _sanitize_for_pdf(_clean_inline_md(block["text"]))

            if btype == "table_row":
                table_rows.append(text)
                continue
            else:
                if table_rows:
                    _render_pdf_table(pdf, table_rows)
                    table_rows = []

            pdf.set_x(pdf.l_margin)

            if btype == "blank":
                pdf.ln(3)
                continue

            if btype == "h1":
                pdf.ln(5)
                pdf.set_font("Helvetica", "B", 18)
                pdf.set_text_color(20, 20, 20)
                pdf.multi_cell(0, 9, text)
                pdf.ln(3)
            elif btype == "h2":
                pdf.ln(4)
                pdf.set_font("Helvetica", "B", 15)
                pdf.set_text_color(40, 40, 40)
                pdf.multi_cell(0, 8, text)
                pdf.ln(2)
            elif btype == "h3":
                pdf.ln(3)
                pdf.set_font("Helvetica", "B", 13)
                pdf.set_text_color(50, 50, 50)
                pdf.multi_cell(0, 7, text)
                pdf.ln(2)
            elif btype in ("h4", "h5", "h6"):
                pdf.set_font("Helvetica", "B", 11)
                pdf.set_text_color(60, 60, 60)
                pdf.multi_cell(0, 6, text)
                pdf.ln(1)
            elif btype == "bullet":
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(50, 50, 50)
                pdf.multi_cell(0, 5, f"  - {text}")
            elif btype == "numbered":
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(50, 50, 50)
                pdf.multi_cell(0, 5, text)
            elif btype == "hr":
                pdf.ln(2)
                y = pdf.get_y()
                pdf.set_draw_color(200, 200, 200)
                pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
                pdf.ln(2)
            else:
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(50, 50, 50)
                pdf.multi_cell(0, 5, text)
                pdf.ln(1)

        if table_rows:
            _render_pdf_table(pdf, table_rows)

    return bytes(pdf.output())


def _render_pdf_table(pdf: FPDF, rows: list[str]):
    parsed = []
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if all(re.match(r"^[-:]+$", c) for c in cells):
            continue
        parsed.append(cells)

    if not parsed:
        return

    cols = len(parsed[0])
    page_width = pdf.w - pdf.l_margin - pdf.r_margin
    col_width = page_width / cols

    for i, row_data in enumerate(parsed):
        if i == 0:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_fill_color(240, 240, 240)
        else:
            pdf.set_font("Helvetica", "", 9)
            pdf.set_fill_color(255, 255, 255)

        pdf.set_text_color(50, 50, 50)
        for j, cell_text in enumerate(row_data):
            if j < cols:
                clean = _sanitize_for_pdf(_clean_inline_md(cell_text))
                pdf.cell(col_width, 6, clean[:40], border=1, fill=True)
        pdf.ln()

    pdf.ln(3)
