"""Reading the original notice: web page, attachments, HWP/HWPX text and the public-host guard."""

import io
import struct
import zipfile

import pytest

from spec_agent.services import notice_original as original
from spec_agent.services.notice_original import OriginalError, fetch_original, read_file

PUBLIC = [(2, 1, 6, "", ("203.0.113.10", 443))]


def public(host, port):
    return [(2, 1, 6, "", ("8.8.8.8", port))]


def hwpx(*paragraphs: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        body = "".join(f"<hp:p><hp:run><hp:t>{p}</hp:t></hp:run></hp:p>" for p in paragraphs)
        archive.writestr(
            "Contents/section0.xml", f'<hs:sec xmlns:hp="x" xmlns:hs="y">{body}</hs:sec>'
        )
    return buffer.getvalue()


class Response:
    def __init__(self, url, body, content_type="text/html; charset=utf-8", disposition=""):
        self.url, self.body = url, body
        self.headers = {"Content-Type": content_type}
        if disposition:
            self.headers["Content-Disposition"] = disposition

    def read(self, limit):
        return self.body[:limit]

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_hwp_paragraph_records_become_text():
    text = "신청자격: 조선기자재 기업"
    payload = b"\x02\x00" + b"\x00" * 14 + text.encode("utf-16-le") + b"\x0d\x00"
    record = struct.pack("<I", 67 | (len(payload) << 20)) + payload
    other = struct.pack("<I", 66 | (4 << 20)) + b"\x00" * 4
    assert original._hwp_records(other + record) == [text + "\n"]


def test_hwpx_and_text_files_are_read():
    assert "임금 체불 사업장 제외" in read_file(
        "notice.hwpx", hwpx("신청자격", "임금 체불 사업장 제외")
    )
    assert read_file("x", hwpx("A &amp; B")) == "A & B"  # sniffed from the contents
    assert "경남" in read_file("a.txt", "경남 소재".encode("cp949"))
    with pytest.raises(OriginalError):
        read_file("photo.png", b"\x89PNG")


def test_page_and_attachments_are_fetched_and_named():
    page = """<html><head><script>var x = 1;</script></head><body><nav>메뉴</nav>
      <div class="view"><h3>2026 조선기자재 지원사업</h3><p>사업 개요 안내</p></div>
      <a href="/cmm/fms/getImageFile.do?atchFileId=F1&fileSn=0">공고문.hwpx</a>
      <a href="/cmm/fms/getImageFile.do?atchFileId=F1&fileSn=1">공고문.pdf</a>
      <a href="/files/form.txt">신청서 양식.txt</a>
      <a href="javascript:void(0)">인쇄</a></body></html>""".encode()
    served = {
        "https://www.bizinfo.go.kr/view?id=1": Response(
            "https://www.bizinfo.go.kr/view?id=1", page
        ),
        "https://www.bizinfo.go.kr/cmm/fms/getImageFile.do?atchFileId=F1&fileSn=0": Response(
            "https://www.bizinfo.go.kr/cmm/fms/getImageFile.do?atchFileId=F1&fileSn=0",
            hwpx("신청자격: 동남권 소재 기업", "제외: 국세 체납"),
            "application/octet-stream",
            "attachment; filename*=UTF-8''%EA%B3%B5%EA%B3%A0%EB%AC%B8.hwpx",
        ),
        "https://www.bizinfo.go.kr/cmm/fms/getImageFile.do?atchFileId=F1&fileSn=1": Response(
            "https://www.bizinfo.go.kr/cmm/fms/getImageFile.do?atchFileId=F1&fileSn=1",
            b"not really a pdf",
            "application/pdf",
        ),
        "https://www.bizinfo.go.kr/files/form.txt": Response(
            "https://www.bizinfo.go.kr/files/form.txt", "회사명:".encode(), "text/plain"
        ),
    }
    requested = []

    def opener(request, timeout):
        requested.append(request.full_url)
        return served[request.full_url]

    found = fetch_original("https://www.bizinfo.go.kr/view?id=1", opener, public)
    names = [f["name"] for f in found["files"]]
    assert names == ["공고문.hwpx", "신청서 양식.txt"]
    assert found["skipped"] == ["공고문.pdf"]  # broken PDF is skipped, not fatal
    assert found["text"].index("동남권 소재") < found["text"].index("사업 개요")  # files first
    assert "var x" not in found["text"] and "메뉴" not in found["text"]
    assert not any("javascript" in url for url in requested)


def test_private_addresses_are_refused():
    def local(host, port):
        return [(2, 1, 6, "", ("127.0.0.1", port))]

    def never(request, timeout):
        raise AssertionError("must not connect")

    with pytest.raises(OriginalError, match="public"):
        fetch_original("http://intranet.example/notice", never, local)
    with pytest.raises(OriginalError, match="http"):
        fetch_original("file:///etc/passwd", never, public)
