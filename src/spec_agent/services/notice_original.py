"""Fetch and read the original text of a support-programme notice.

A feed item only carries a short summary; the real eligibility rules live on the notice page and
in its attachments (usually HWP or PDF). This module downloads the page, follows up to three
attachment links, and turns everything into plain text that the AI check can read.

Only public http(s) hosts are contacted: every address, including redirects, is resolved and
private, loopback and link-local addresses are refused.
"""

import ipaddress
import re
import socket
import struct
import urllib.parse
import urllib.request
import zipfile
import zlib
from html.parser import HTMLParser
from io import BytesIO
from typing import ClassVar

from spec_agent.services.documents import _decode, _docx, _pdf

MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 3
MAX_CHARS = 60_000
FILE_SUFFIXES = (".pdf", ".hwpx", ".hwp", ".docx", ".txt")
DOWNLOAD_HINTS = ("filedown", "download", "getimagefile", "atchfile", "fileid")
TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (compatible; YardMate notice reader)"


class OriginalError(ValueError):
    """The original notice could not be fetched or read."""


# ---------------------------------------------------------------- file formats


def _hwp(content: bytes) -> str:
    """Read the body text of an HWP 5 file (OLE container, zlib-compressed sections)."""
    import olefile

    ole = olefile.OleFileIO(BytesIO(content))
    header = ole.openstream("FileHeader").read()
    flags = struct.unpack_from("<I", header, 36)[0]
    if flags & 0x2:
        raise OriginalError("The HWP file is password protected.")
    compressed = bool(flags & 0x1)
    sections = sorted(
        (path for path in ole.listdir() if path[0] == "BodyText"),
        key=lambda path: int(re.sub(r"\D", "", path[1]) or 0),
    )
    if not sections:
        raise OriginalError("The HWP file has no readable body (it may be a view-only copy).")
    paragraphs = []
    for path in sections:
        data = ole.openstream(path).read()
        if compressed:
            data = zlib.decompress(data, -15)
        paragraphs.extend(_hwp_records(data))
    return "\n".join(paragraphs)


def _hwp_records(data: bytes) -> list[str]:
    paragraphs, offset = [], 0
    while offset + 4 <= len(data):
        head = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        tag, size = head & 0x3FF, (head >> 20) & 0xFFF
        if size == 0xFFF:
            size = struct.unpack_from("<I", data, offset)[0]
            offset += 4
        if tag == 67:  # HWPTAG_PARA_TEXT
            text = _hwp_text(data[offset : offset + size])
            if text.strip():
                paragraphs.append(text)
        offset += size
    return paragraphs


def _hwp_text(payload: bytes) -> str:
    chars, index = [], 0
    while index + 2 <= len(payload):
        code = struct.unpack_from("<H", payload, index)[0]
        if code < 32:
            if code in (0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31):
                if code in (10, 13):
                    chars.append("\n")
                elif code == 30:
                    chars.append("-")
                elif code == 31:
                    chars.append(" ")
                index += 2
            else:  # inline and extended controls take eight characters
                if code == 9:
                    chars.append("\t")
                index += 16
            continue
        chars.append(chr(code))
        index += 2
    return "".join(chars)


def _hwpx(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = sorted(
            (n for n in archive.namelist() if re.match(r"Contents/section\d+\.xml$", n)),
            key=lambda n: int(re.sub(r"\D", "", n)),
        )
        xml = "".join(archive.read(name).decode("utf-8", errors="replace") for name in names)
    xml = re.sub(r"</hp:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"&lt;", "<", re.sub(r"&gt;", ">", re.sub(r"&amp;", "&", text)))


def sniff_suffix(name: str, content: bytes) -> str:
    lowered = name.lower()
    for suffix in FILE_SUFFIXES:
        if lowered.endswith(suffix):
            return suffix
    if content.startswith(b"%PDF"):
        return ".pdf"
    if content.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return ".hwp"
    if content.startswith(b"PK"):
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = archive.namelist()
        except zipfile.BadZipFile:
            return ""
        if any(n.startswith("Contents/section") for n in names):
            return ".hwpx"
        if "word/document.xml" in names:
            return ".docx"
    return ""


def read_file(name: str, content: bytes) -> str:
    """Turn a notice attachment into text; raises OriginalError for unsupported files."""
    suffix = sniff_suffix(name, content)
    readers = {".pdf": _pdf, ".hwp": _hwp, ".hwpx": _hwpx, ".docx": _docx, ".txt": _decode}
    if suffix not in readers:
        raise OriginalError("Use a PDF, HWP, HWPX, DOCX or TXT file.")
    try:
        text = readers[suffix](content)
    except OriginalError:
        raise
    except Exception as error:  # noqa: BLE001 - any parser failure is a user-facing error
        raise OriginalError(f"The file could not be read: {error}") from None
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if not text:
        raise OriginalError("No text was found in the file (it may be a scanned image).")
    return text


# ---------------------------------------------------------------- web pages


class _PageText(HTMLParser):
    SKIP: ClassVar[set[str]] = {
        "script",
        "style",
        "noscript",
        "header",
        "footer",
        "nav",
        "svg",
        "form",
        "select",
    }
    BLOCK: ClassVar[set[str]] = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "td",
        "th",
        "dd",
        "dt",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.skip = 0
        self.href: str | None = None
        self.anchor: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.anchor = []

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "a" and self.href is not None:
            self.links.append((self.href, " ".join("".join(self.anchor).split())))
            self.href = None

    def handle_data(self, data):
        if self.href is not None:
            self.anchor.append(data)
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return "\n".join(line for line in lines if len(line) > 1)


def _check_host(url: str, resolve) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise OriginalError("Only http(s) links can be fetched.")
    try:
        infos = resolve(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError:
        raise OriginalError(f"The site {parts.hostname} could not be found.") from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise OriginalError("That address is not a public website.")


def _opener(resolve):
    class Guard(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _check_host(newurl, resolve)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(Guard).open


def _download(url: str, limit: int, opener, resolve) -> tuple[bytes, str, str]:
    _check_host(url, resolve)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            content = response.read(limit + 1)
            headers = response.headers
            final = response.geturl() if hasattr(response, "geturl") else url
    except OriginalError:
        raise
    except Exception as error:  # noqa: BLE001 - network failures become one readable error
        raise OriginalError(f"The page could not be opened: {error}") from None
    if len(content) > limit:
        raise OriginalError("The file is too large to read.")
    disposition = headers.get("Content-Disposition", "") if headers else ""
    name = ""
    match = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", disposition, re.IGNORECASE)
    if match:
        name = urllib.parse.unquote(match.group(1).strip('"'))
    else:
        match = re.search(r'filename="?([^";]+)', disposition, re.IGNORECASE)
        if match:
            raw = match.group(1)
            try:
                name = urllib.parse.unquote(raw.encode("latin-1").decode("utf-8"))
            except (UnicodeEncodeError, UnicodeDecodeError):
                name = urllib.parse.unquote(raw)
    content_type = headers.get("Content-Type", "") if headers else ""
    return content, f"{content_type}\n{name}", final


def _attachment_links(links: list[tuple[str, str]], base: str) -> list[tuple[str, str]]:
    found, seen = [], set()
    for href, label in links:
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        target = urllib.parse.urljoin(base, href)
        path = urllib.parse.urlsplit(target).path.lower()
        named = any(label.lower().endswith(s) or path.endswith(s) for s in FILE_SUFFIXES)
        hinted = any(hint in target.lower() for hint in DOWNLOAD_HINTS)
        if (named or hinted) and target not in seen:
            seen.add(target)
            found.append((target, label))
    # Prefer formats we read best, so a notice offered as HWP and PDF is read once as PDF.
    rank = {".pdf": 0, ".hwpx": 1, ".hwp": 2, ".docx": 3, ".txt": 4}

    def order(item):
        label = item[1].lower()
        return next((rank[s] for s in rank if label.endswith(s)), 5)

    return sorted(found, key=order)


def fetch_original(url: str, opener=None, resolve=socket.getaddrinfo) -> dict:
    """Download a notice page and its attachments and return their text.

    Returns {"text", "source", "files": [{"name", "chars"}], "skipped": [name]}; attachments
    come first because they hold the full notice, then the page text.
    """
    opener = opener or _opener(resolve)
    content, meta, final = _download(url, MAX_PAGE_BYTES, opener, resolve)
    content_type, disposition = meta.split("\n", 1)
    files, skipped, sections = [], [], []

    if "html" not in content_type.lower() and sniff_suffix(disposition or final, content):
        name = disposition or urllib.parse.unquote(final.rsplit("/", 1)[-1]) or "notice"
        text = read_file(name, content)
        return {
            "text": text[:MAX_CHARS],
            "source": url,
            "files": [{"name": name, "chars": len(text)}],
            "skipped": [],
        }

    page = _PageText()
    page.feed(_decode(content))
    page_text = page.text()
    read_names: set[str] = set()
    for target, label in _attachment_links(page.links, final):
        if len(files) >= MAX_FILES:
            break
        try:
            data, file_meta, file_url = _download(target, MAX_FILE_BYTES, opener, resolve)
            name = file_meta.split("\n", 1)[1] or label or file_url.rsplit("/", 1)[-1]
            stem = re.sub(r"\.[a-z0-9]+$", "", name.lower())
            if stem and stem in read_names:
                continue  # the same notice in another format
            text = read_file(name, data)
        except OriginalError:
            if label:
                skipped.append(label)
            continue
        read_names.add(stem)
        files.append({"name": name, "chars": len(text)})
        sections.append(f"[{name}]\n{text}")

    if page_text:
        sections.append(f"[{final}]\n{page_text}")
    text = "\n\n".join(sections).strip()
    if not text:
        raise OriginalError("No notice text was found on the page.")
    return {"text": text[:MAX_CHARS], "source": url, "files": files, "skipped": skipped[:5]}
