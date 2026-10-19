import pymupdf as fitz
import pytest

from atlasdoc.cli import combine_markdown, main


def _pdf_bytes() -> bytes:
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), "Deneme")
        return doc.tobytes()
    finally:
        doc.close()


def _write_pdf(path):
    path.write_bytes(_pdf_bytes())


class TestCombineMarkdown:
    def test_joins_pages_and_optional_separators(self):
        pages = [(1, "# A\n"), (2, "B")]
        assert combine_markdown(pages, separators=False) == "# A\n\nB\n"
        assert combine_markdown(pages, separators=True) == (
            "<!-- Sayfa 1 -->\n# A\n\n---\n\n<!-- Sayfa 2 -->\nB\n"
        )

    def test_skips_blank_pages(self):
        assert combine_markdown([(1, "  "), (2, "ok")], separators=False) == "ok\n"


class TestConvertCommand:
    def test_help_exits_cleanly(self):
        with pytest.raises(SystemExit) as exc:
            main(["convert", "--help"])
        assert exc.value.code == 0
        with pytest.raises(SystemExit) as serve_exc:
            main(["serve", "--help"])
        assert serve_exc.value.code == 0

    def test_writes_markdown_from_the_pipeline(self, tmp_path, monkeypatch):
        source = tmp_path / "in.pdf"
        _write_pdf(source)
        output = tmp_path / "nested" / "out.md"
        seen = {}

        async def fake(**kwargs):
            seen.update(kwargs)
            yield {"type": "init", "total_pages": 2}
            yield {"type": "page_done", "page": 1, "markdown": "# One"}
            yield {"type": "page_done", "page": 2, "markdown": "Two", "truncated": True}
            yield {
                "type": "complete",
                "ok_pages": [1, 2],
                "failed_pages": [],
                "truncated_pages": [2],
            }

        monkeypatch.setattr("atlasdoc.cli.process_pdf_stream", fake)
        monkeypatch.setenv("ATLASDOC_BASE_URL", "http://host.docker.internal:1234/v1")
        monkeypatch.setenv("ATLASDOC_MODEL", "demo-model")
        code = main(
            [
                "convert",
                str(source),
                "-o",
                str(output),
                "--pages",
                "1-2",
                "--dpi",
                "72",
                "--page-separators",
            ]
        )
        assert code == 0
        assert output.read_text(encoding="utf-8") == (
            "<!-- Sayfa 1 -->\n# One\n\n---\n\n<!-- Sayfa 2 -->\nTwo\n"
        )
        assert seen["base_url"] == "http://host.docker.internal:1234/v1"
        assert seen["model_name"] == "demo-model"
        assert seen["page_ranges"] == "1-2"
        assert seen["dpi"] == 72

    def test_explicit_base_url_overrides_env(self, tmp_path, monkeypatch):
        source = tmp_path / "in.pdf"
        _write_pdf(source)
        output = tmp_path / "out.md"
        seen = {}

        async def fake(**kwargs):
            seen.update(kwargs)
            yield {"type": "page_done", "page": 1, "markdown": "ok"}
            yield {"type": "complete", "ok_pages": [1], "failed_pages": [], "truncated_pages": []}

        monkeypatch.setattr("atlasdoc.cli.process_pdf_stream", fake)
        monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://lmstudio.example/v1")
        monkeypatch.delenv("ATLASDOC_BASE_URL", raising=False)
        code = main(
            ["convert", str(source), "-o", str(output), "--base-url", "http://override/v1"]
        )
        assert code == 0
        assert seen["base_url"] == "http://override/v1"

        seen.clear()
        code = main(["convert", str(source), "-o", str(output)])
        assert code == 0
        assert seen["base_url"] == "http://lmstudio.example/v1"

    def test_failed_page_still_writes_and_exits_nonzero(self, tmp_path, monkeypatch):
        source = tmp_path / "in.pdf"
        _write_pdf(source)
        output = tmp_path / "out.md"

        async def fake(**kwargs):
            yield {"type": "page_done", "page": 1, "markdown": "kept"}
            yield {"type": "page_error", "page": 2, "message": "Sayfa 2 zaman aşımına uğradı"}
            yield {"type": "complete", "ok_pages": [1], "failed_pages": [2], "truncated_pages": []}

        monkeypatch.setattr("atlasdoc.cli.process_pdf_stream", fake)
        code = main(["convert", str(source), "-o", str(output)])
        assert code == 1
        assert output.read_text(encoding="utf-8") == "kept\n"

    def test_fatal_error_without_text_does_not_write(self, tmp_path, monkeypatch):
        source = tmp_path / "in.pdf"
        _write_pdf(source)
        output = tmp_path / "out.md"

        async def fake(**kwargs):
            yield {"type": "error", "message": "bağlantı koptu"}

        monkeypatch.setattr("atlasdoc.cli.process_pdf_stream", fake)
        code = main(["convert", str(source), "-o", str(output)])
        assert code == 1
        assert not output.exists()

    def test_rejects_missing_non_pdf_and_oversized_files(self, tmp_path, monkeypatch):
        missing = main(["convert", str(tmp_path / "nope.pdf"), "-o", str(tmp_path / "a.md")])
        assert missing == 1

        text = tmp_path / "notes.txt"
        text.write_text("hello", encoding="utf-8")
        assert main(["convert", str(text), "-o", str(tmp_path / "b.md")]) == 1

        monkeypatch.setenv("ATLASDOC_MAX_UPLOAD_MB", "1")
        big = tmp_path / "big.pdf"
        big.write_bytes(b"%PDF-1.4\n" + b"0" * (1024 * 1024))
        called = {"n": 0}

        async def fake(**kwargs):
            called["n"] += 1
            yield {"type": "complete", "ok_pages": [], "failed_pages": [], "truncated_pages": []}

        monkeypatch.setattr("atlasdoc.cli.process_pdf_stream", fake)
        assert main(["convert", str(big), "-o", str(tmp_path / "c.md")]) == 1
        assert called["n"] == 0
