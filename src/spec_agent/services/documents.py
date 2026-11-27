"""Text extraction for revision request attachments (request letters, change notices, mail)."""

import re
import zipfile
from email import policy
from email.parser import BytesParser
from io import BytesIO
from xml.etree import ElementTree

MAX_REQUEST_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 20000
REQUEST_SUFFIXES = {".pdf", ".xlsx", ".docx", ".txt", ".md", ".eml"}


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _pdf(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _xlsx(content: bytes) -> str:
    from openpyxl import load_workbook

    book = load_workbook(BytesIO(content), read_only=True, data_only=True)
    lines = []
    for sheet in book.worksheets:
        for row in sheet.iter_rows(values_only=True):
            cells = [str(value) for value in row if value not in (None, "")]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _docx(content: bytes) -> str:
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(BytesIO(content)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.iter(namespace + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _eml(content: bytes) -> str:
    message = BytesParser(policy=policy.default).parsebytes(content)
    parts = [f"Subject: {message.get('subject', '')}", f"From: {message.get('from', '')}"]
    body = message.get_body(preferencelist=("plain", "html"))
    if body is not None:
        text = body.get_content()
        if body.get_content_subtype() == "html":
            text = re.sub(r"<[^>]+>", " ", text)
        parts.append(text)
    return "\n".join(parts)


def validate_request_file(filename: str, content: bytes) -> str:
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if suffix not in REQUEST_SUFFIXES:
        raise ValueError("Attach a PDF, XLSX, DOCX, EML, TXT, or MD request file.")
    if len(content) > MAX_REQUEST_BYTES:
        raise ValueError("Files must not exceed 20 MB.")
    signatures = {".pdf": b"%PDF-", ".xlsx": b"PK", ".docx": b"PK"}
    if suffix in signatures and not content.startswith(signatures[suffix]):
        raise ValueError("Check the file format.")
    return suffix


def extract_text(suffix: str, content: bytes) -> str:
    try:
        text = {
            ".pdf": _pdf,
            ".xlsx": _xlsx,
            ".docx": _docx,
            ".eml": _eml,
        }.get(suffix, lambda data: _decode(data))(content)
    except Exception as error:  # noqa: BLE001 - surface any parser failure as a user error
        raise ValueError(f"The request file could not be read: {error}") from None
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text[:MAX_TEXT_CHARS]
