import pymupdf as fitz
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import _ensure_content_length_within_limit, app, max_upload_bytes


def _tiny_pdf() -> bytes:
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), "Deneme")
        return doc.tobytes()
    finally:
        doc.close()


def _request_with_length(length: int) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/pdf-info",
        "raw_path": b"/api/pdf-info",
        "query_string": b"",
        "headers": [(b"content-length", str(length).encode())],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }
    return Request(scope)


class TestUploadLimit:
    def test_default_and_env(self, monkeypatch):
        monkeypatch.delenv("ATLASDOC_MAX_UPLOAD_MB", raising=False)
        assert max_upload_bytes() == 100 * 1024 * 1024
        monkeypatch.setenv("ATLASDOC_MAX_UPLOAD_MB", "12")
        assert max_upload_bytes() == 12 * 1024 * 1024
        monkeypatch.setenv("ATLASDOC_MAX_UPLOAD_MB", "nope")
        assert max_upload_bytes() == 100 * 1024 * 1024
        monkeypatch.setenv("ATLASDOC_MAX_UPLOAD_MB", "0")
        assert max_upload_bytes() == 1024 * 1024

    def test_content_length_header_is_rejected(self):
        with pytest.raises(HTTPException) as caught:
            _ensure_content_length_within_limit(_request_with_length(20 * 1024 * 1024), 1024 * 1024)
        assert caught.value.status_code == 413

    def test_pdf_info_rejects_body_over_the_limit(self, monkeypatch):
        monkeypatch.setenv("ATLASDOC_MAX_UPLOAD_MB", "1")
        client = TestClient(app)
        payload = b"%PDF-1.4\n" + (b"0" * (1024 * 1024))
        response = client.post(
            "/api/pdf-info",
            files={"file": ("buyuk.pdf", payload, "application/pdf")},
        )
        assert response.status_code == 413
        assert "1 MB" in response.json()["detail"]

    def test_convert_rejects_the_same_limit(self, monkeypatch):
        monkeypatch.setenv("ATLASDOC_MAX_UPLOAD_MB", "1")
        client = TestClient(app)
        payload = b"%PDF-1.4\n" + (b"0" * (1024 * 1024))
        response = client.post(
            "/api/convert",
            files={"file": ("buyuk.pdf", payload, "application/pdf")},
            data={"page_ranges": "1"},
        )
        assert response.status_code == 413

    def test_small_pdf_is_accepted_and_non_pdf_is_rejected(self):
        client = TestClient(app)
        ok = client.post(
            "/api/pdf-info",
            files={"file": ("kucuk.pdf", _tiny_pdf(), "application/pdf")},
        )
        assert ok.status_code == 200
        assert ok.json()["total_pages"] == 1

        rejected = client.post(
            "/api/pdf-info",
            files={"file": ("not.txt", b"merhaba", "text/plain")},
        )
        assert rejected.status_code == 400
