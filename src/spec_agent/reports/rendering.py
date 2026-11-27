import hashlib
import json
import math
import os
import unicodedata
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from spec_agent.reports.i18n import ReportTranslator, rich_text

FONT_DIR = Path(__file__).with_name("fonts")
LATIN_FONT = "SpecAgentSans"


def font_name(font_path: Path | None) -> str:
    if font_path:
        if not font_path.is_file():
            raise ValueError("No TTF font was found at SPEC_AGENT_FONT.")
        name = "Korean-" + hashlib.sha256(str(font_path).encode()).hexdigest()[:12]
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(font_path)))
        return name
    name = "HYSMyeongJo-Medium"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(name))
    return name


CHINESE_FONT = "STSong-Light"


def chinese_font() -> str:
    """Adobe-GB1 CID font so simplified Chinese glyphs render (the Korean font lacks many)."""
    if CHINESE_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(CHINESE_FONT))
    return CHINESE_FONT


def cjk_font(base: str, language: str) -> str:
    """Chinese reports use the Chinese font, with Korean customer wording in the Korean font."""
    return f"{chinese_font()}+{base}" if language == "zh" else base


def latin_font() -> str:
    """Embedded TrueType font covering Latin; CJK runs use the Korean or Chinese font."""
    if LATIN_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(LATIN_FONT, str(FONT_DIR / "DejaVuSansCondensed.ttf")))
    return LATIN_FONT


def report_rows(
    state: dict, approved: bool, language: str = "en"
) -> tuple[list[list[str]], list[list[str]], dict]:
    tr = ReportTranslator(language)
    standard, inputs = state["standard"], state["inputs"]
    metadata = [
        [tr.label("Report ID"), state["inspection_id"]],
        [tr.label("Status"), tr.label("Approved" if approved else "Unapproved draft")],
        [tr.label("Input revision"), str(state["input_revision"])],
        [
            tr.label("Customer / Product"),
            f"{standard['customer']} / {tr.term(standard['product_name'])}",
        ],
        [tr.label("Part number / Lot"), f"{standard['part_number']} / {inputs['lot']}"],
        [
            tr.label("Standard / Revision"),
            f"{standard['document_number']} / {standard['revision']}",
        ],
        [
            tr.label("Inspection date / Inspector"),
            f"{inputs['inspection_date']} / {inputs['inspector']}",
        ],
        [tr.label("Overall decision"), tr.decision(state["overall_result"])],
        [
            tr.label("Reviewer / Approver"),
            f"{state['reviewer']} / {state['approver'] if approved else tr.label('Not approved')}",
        ],
        [tr.label("Approval time"), state["events"][-1]["at"] if approved else "-"],
        [
            tr.label("Data provenance"),
            tr.label(
                "Synthetic data / Not company-approved"
                if standard["provenance"] == "SYNTHETIC"
                else "Internal reference"
            ),
        ],
    ]
    if not tr.english:
        metadata.append([tr.label("Language"), language])
    rows = [
        [
            tr.label("Area / Item"),
            tr.label("Criteria"),
            tr.label("Measurement / Observation"),
            tr.label("Automated / Final"),
            tr.label("Explanation / Source"),
        ]
    ]
    for result in state["item_results"]:
        warning = "\n" + tr.label("[Warning]") if result["warning"] else ""
        final = result["final_result"] or "N_A"
        rows.append(
            [
                f"{tr.term(result['area'])} /\n{tr.term(result['item'])}",
                tr.phrase(result["criteria"]),
                tr.phrase(result["measured"]),
                f"{tr.decision(result['auto_result'])} / {tr.decision(final)}{warning}",
                (
                    f"{tr.sentence(result['explanation'])}\n{result['source']}\n"
                    f"{tr.label(result['review_reason'])}"
                ),
            ]
        )
    texts = {
        "title": tr.label("Inspection Report"),
        "notice": tr.label("Provisional template / Development use · Not an electronic signature"),
        "translated": ""
        if tr.english
        else tr.label(
            "Translated from the English master. Customer sources and user-entered text are "
            "kept as written."
        ),
        "signatures": f"{tr.label('Prepared by')}: __________________    "
        f"{tr.label('Approved by')}: __________________",
    }
    return metadata, rows, texts


def render_pdf(path: Path, metadata: list, rows: list, font: str, texts: dict | None = None):
    texts = texts or {
        "title": "Inspection Report",
        "notice": "Provisional template / Development use · Not an electronic signature",
        "translated": "",
        "signatures": "Prepared by: __________________    Approved by: __________________",
    }
    style = ParagraphStyle("body", fontName=latin_font(), fontSize=8, leading=12)
    heading = ParagraphStyle("heading", parent=style, fontSize=19, leading=26, spaceAfter=12)

    def paragraph(value, paragraph_style=style):
        return Paragraph(rich_text(value, font), paragraph_style)

    story = [
        paragraph(texts["title"], heading),
        paragraph(texts["notice"]),
        *([paragraph(texts["translated"])] if texts["translated"] else []),
        Spacer(1, 12),
    ]
    details = Table([[paragraph(value) for value in row] for row in metadata], colWidths=[105, 418])
    details.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef3f5")),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5db")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([details, Spacer(1, 18)])
    table = Table(
        [[paragraph(value) for value in row] for row in rows],
        colWidths=[104, 100, 80, 66, 173],
        repeatRows=1,
        splitInRow=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbeee8")),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5db")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend(
        [
            table,
            Spacer(1, 14),
            paragraph(texts["signatures"]),
        ]
    )

    def footer(canvas, document):
        canvas.setFont(latin_font(), 8)
        canvas.drawRightString(A4[0] - 36, 22, f"{document.page} / YardMate")

    SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=32,
        bottomMargin=36,
        title=texts["title"],
    ).build(story, onFirstPage=footer, onLaterPages=footer)


def render_excel(path: Path, metadata: list, rows: list, texts: dict | None = None):
    texts = texts or {
        "title": "Inspection Report",
        "notice": "Provisional template / Development use · Not an electronic signature",
        "translated": "",
    }
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = texts["title"][:31]
    sheet.append(
        [
            " · ".join(
                part for part in (texts["title"], texts["notice"], texts["translated"]) if part
            )
        ]
    )
    sheet.merge_cells("A1:E1")
    for row in metadata:
        sheet.append(row)
    sheet.append([])
    header_row = len(metadata) + 3
    for row in rows:
        chunks = [
            [value[index : index + 250] for index in range(0, len(value), 250)] or [""]
            for value in row
        ]
        for index in range(max(map(len, chunks))):
            sheet.append([parts[index] if index < len(parts) else "" for parts in chunks])
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.font = Font(name="Arial", size=10)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for row_index in (1, header_row):
        for cell in sheet[row_index]:
            cell.fill = PatternFill("solid", fgColor="DBEEE8")
            cell.font = Font(name="Arial", size=11, bold=True)
    for column, width in zip("ABCDE", (25, 40, 30, 23, 65)):
        sheet.column_dimensions[column].width = width
    sheet.row_dimensions[1].height = 28
    widths = (25, 40, 30, 23, 65)
    for row in sheet.iter_rows(min_row=2):
        line_counts = []
        for cell, width in zip(row, widths):
            line_counts.append(
                sum(
                    max(
                        1,
                        math.ceil(
                            sum(
                                2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
                                for character in line
                            )
                            / (width - 2)
                        ),
                    )
                    for line in str(cell.value or "").split("\n")
                )
            )
        sheet.row_dimensions[row[0].row].height = min(409, max(24, max(line_counts) * 15 + 10))
    sheet.freeze_panes = f"C{header_row + 1}"
    sheet.auto_filter.ref = f"A{header_row}:E{sheet.max_row}"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = f"{header_row}:{header_row}"
    workbook.save(path)


def file_key(extension: str, language: str) -> str:
    return extension if language == "en" else f"{extension}-{language}"


class ReportRenderer:
    def __init__(self, root: Path, font_path: Path | None, languages: tuple[str, ...] = ("en",)):
        self.root = root
        self.font = font_name(font_path)
        latin_font()
        self.languages = ("en", *[language for language in languages if language != "en"])

    def __call__(self, state: dict, approved: bool) -> dict:
        variants = {language: report_rows(state, approved, language) for language in self.languages}
        content = json.dumps(variants, ensure_ascii=False, sort_keys=True).encode()
        digest = hashlib.sha256(content).hexdigest()
        stage = "approved" if approved else "draft"
        revision = f"{state['input_revision']}-{stage}-{digest[:12]}"
        directory = self.root / state["inspection_id"] / revision
        directory.mkdir(parents=True, exist_ok=True)
        files = {}
        for language, (metadata, rows, texts) in variants.items():
            stem = "report" if language == "en" else f"report.{language}"
            for extension, render in [
                (
                    "pdf",
                    lambda path, m=metadata, r=rows, x=texts, language=language: render_pdf(
                        path, m, r, cjk_font(self.font, language), x
                    ),
                ),
                ("xlsx", lambda path, m=metadata, r=rows, x=texts: render_excel(path, m, r, x)),
            ]:
                target = directory / f"{stem}.{extension}"
                if not target.exists():
                    temporary = directory / f"{stem}.tmp.{extension}"
                    render(temporary)
                    os.replace(temporary, target)
                files[file_key(extension, language)] = {
                    "path": str(target.relative_to(self.root)),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
        return {
            "revision": revision,
            "approved": approved,
            "content_hash": digest,
            "files": files,
            "approval": {
                "user_id": state.get("approver_user_id"),
                "name": state["approver"],
                "at": state["events"][-1]["at"],
            }
            if approved
            else None,
        }
