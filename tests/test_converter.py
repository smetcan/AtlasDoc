import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import patch

import httpx2
import pymupdf as fitz
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError

from converter import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_TOKENS,
    DEFAULT_REQUEST_TIMEOUT,
    check_provider_health,
    clean_llm_markdown,
    parse_page_ranges,
    process_pdf_stream,
    resolve_provider_config,
)


def _pdf_bytes(pages: int = 2) -> bytes:
    doc = fitz.open()
    try:
        for index in range(pages):
            page = doc.new_page()
            page.insert_text((72, 72), f"Sayfa {index + 1}")
        return doc.tobytes()
    finally:
        doc.close()


def _request() -> httpx2.Request:
    return httpx2.Request("POST", "http://localhost:1234/v1/chat/completions")


def _completion(text: str, finish_reason: str = "stop"):
    message = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


class _Completions:
    def __init__(self, effects):
        self.effects = list(effects)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        effect = self.effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class _Client:
    def __init__(self, effects):
        self.chat = SimpleNamespace(completions=_Completions(effects))


def _collect(pdf: bytes, effects, **kwargs):
    holder = {}

    def factory(*args, **factory_kwargs):
        client = _Client(effects)
        client.init_args = args
        client.init_kwargs = factory_kwargs
        holder["client"] = client
        return client

    async def run():
        events = []
        with patch("converter.AsyncOpenAI", factory):
            async for event in process_pdf_stream(pdf, dpi=72, **kwargs):
                events.append(event)
        return events

    return asyncio.run(run()), holder.get("client")


class TestCleanMarkdown:
    def test_preserves_nested_list_indentation(self):
        raw = "- a\n    - nested"
        assert clean_llm_markdown(raw) == raw

    def test_preserves_fenced_code_and_mermaid(self):
        raw = (
            "Giriş cümlesi  burada .\n\n"
            "```python\n"
            "def f():\n"
            "    return 1 ;\n"
            "```\n\n"
            "```mermaid\n"
            "flowchart TD\n"
            "    A --> B\n"
            "```"
        )
        cleaned = clean_llm_markdown(raw)
        assert "Giriş cümlesi burada." in cleaned
        assert "def f():\n    return 1 ;" in cleaned
        assert "    A --> B" in cleaned

    def test_does_not_unwrap_a_language_fence(self):
        raw = "```python\ndef f():\n    return 1 ;\n```"
        assert clean_llm_markdown(raw) == raw

    def test_collapses_inline_spaces_but_keeps_hard_breaks(self):
        assert clean_llm_markdown("Merhaba    dünya") == "Merhaba dünya"
        assert clean_llm_markdown("satır  \ndevam") == "satır  \ndevam"

    def test_does_not_delete_repeated_phrases(self):
        raw = "Madde 5 iptal. Yeni Madde 5 metni burada."
        assert clean_llm_markdown(raw) == raw

    def test_still_removes_strike_markup_outside_fences(self):
        assert clean_llm_markdown("Yeni ~~eski~~ metin") == "Yeni metin"
        assert clean_llm_markdown("Yeni <del>eski</del> metin") == "Yeni metin"
        fenced = "```\nkeep ~~this~~  spacing\n```"
        assert clean_llm_markdown(fenced) == "```\nkeep ~~this~~  spacing\n```"

    def test_unwraps_outer_markdown_fence_without_losing_indent(self):
        raw = "```markdown\n- a\n    - nested\n```"
        assert clean_llm_markdown(raw) == "- a\n    - nested"

    def test_removes_think_blocks_without_stripping_indent(self):
        raw = "<think>gizli</think>\n\n    - nested"
        assert clean_llm_markdown(raw) == "    - nested"

    def test_unclosed_fence_is_left_intact(self):
        raw = "önce    sonra\n```python\n    return 1 ;"
        cleaned = clean_llm_markdown(raw)
        assert cleaned.startswith("önce sonra\n")
        assert "    return 1 ;" in cleaned

    def test_empty(self):
        assert clean_llm_markdown("") == ""
        assert clean_llm_markdown("   \n\n") == ""


class TestParsePageRanges:
    def test_empty_means_all_pages(self):
        assert parse_page_ranges("", 4) == [1, 2, 3, 4]
        assert parse_page_ranges("  ", 2) == [1, 2]
        assert parse_page_ranges("all", 2) == [1, 2]
        assert parse_page_ranges("Tümü", 2) == [1, 2]
        assert parse_page_ranges("*", 2) == [1, 2]

    def test_mixed_ranges(self):
        assert parse_page_ranges("1-5, 8, 20-", 22) == [1, 2, 3, 4, 5, 8, 20, 21, 22]

    def test_reversed_range_and_duplicates(self):
        assert parse_page_ranges("5-3, 4, 4", 10) == [3, 4, 5]

    def test_ignores_pages_outside_the_document_when_some_remain(self):
        assert parse_page_ranges("0, 2, 99", 10) == [2]

    def test_rejects_invalid_tokens_and_empty_selection(self):
        with pytest.raises(ValueError):
            parse_page_ranges("abc", 10)
        with pytest.raises(ValueError):
            parse_page_ranges("99", 3)


class TestProcessPdfStream:
    def test_timeout_is_a_connection_subclass_but_skips_only_that_page(self):
        assert issubclass(APITimeoutError, APIConnectionError)
        pdf = _pdf_bytes(2)
        effects = [
            APITimeoutError(_request()),
            _completion("ikinci sayfa"),
        ]
        events, client = _collect(pdf, effects, timeout=12, max_retries=0, max_tokens=128)

        types = [event["type"] for event in events]
        assert types == ["init", "page_start", "page_error", "page_start", "page_done", "complete"]
        assert events[2]["page"] == 1
        assert "12 sn" in events[2]["message"]
        assert events[4]["markdown"] == "ikinci sayfa"
        assert events[4]["truncated"] is False
        complete = events[-1]
        assert complete["failed_pages"] == [1]
        assert complete["ok_pages"] == [2]
        assert complete["truncated_pages"] == []
        assert client.init_kwargs["timeout"] == 12
        assert client.init_kwargs["max_retries"] == 0
        assert client.chat.completions.calls[1]["max_tokens"] == 128

    def test_connection_error_stops_the_job(self):
        pdf = _pdf_bytes(2)
        effects = [APIConnectionError(request=_request()), _completion("ulaşılmamalı")]
        events, client = _collect(pdf, effects, timeout=5, max_retries=0)
        types = [event["type"] for event in events]
        assert types == ["init", "page_start", "error"]
        assert events[-1]["page"] == 1
        assert "bağlantısı koptu" in events[-1]["message"]
        assert len(client.chat.completions.calls) == 1

    def test_status_error_stops_the_job(self):
        pdf = _pdf_bytes(1)
        response = httpx2.Response(503, request=_request())
        effects = [APIStatusError("meşgul", response=response, body=None)]
        events, _client = _collect(pdf, effects, timeout=5, max_retries=0)
        assert [event["type"] for event in events] == ["init", "page_start", "error"]
        assert "503" in events[-1]["message"]

    def test_length_finish_flags_truncated_page(self):
        pdf = _pdf_bytes(1)
        events, client = _collect(
            pdf,
            [_completion("yarım    tablo", finish_reason="length")],
            max_tokens=64,
            timeout=5,
            max_retries=0,
        )
        done = next(event for event in events if event["type"] == "page_done")
        assert done["truncated"] is True
        assert done["finish_reason"] == "length"
        assert done["markdown"] == "yarım tablo"
        complete = events[-1]
        assert complete["ok_pages"] == [1]
        assert complete["failed_pages"] == []
        assert complete["truncated_pages"] == [1]
        assert client.chat.completions.calls[0]["max_tokens"] == 64

    def test_empty_choices_skip_the_page(self):
        pdf = _pdf_bytes(2)
        effects = [SimpleNamespace(choices=[]), _completion("devam")]
        events, _client = _collect(pdf, effects, timeout=5, max_retries=0, max_tokens=32)
        types = [event["type"] for event in events]
        assert types == ["init", "page_start", "page_error", "page_start", "page_done", "complete"]
        assert events[-1]["failed_pages"] == [1]
        assert events[-1]["ok_pages"] == [2]

    def test_render_error_skips_the_page_and_continues(self):
        calls = {"n": 0}

        def flaky_render(pdf_bytes, page_index, dpi):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("pixmap")
            return ("aGVsbG8=", [])

        pdf = _pdf_bytes(2)
        holder = {}

        def factory(*args, **factory_kwargs):
            client = _Client([_completion("ikinci")])
            client.init_kwargs = factory_kwargs
            holder["client"] = client
            return client

        async def run():
            events = []
            with patch("converter.AsyncOpenAI", factory), patch(
                "converter.render_page_from_bytes", flaky_render
            ):
                async for event in process_pdf_stream(pdf, dpi=72, timeout=5, max_retries=0):
                    events.append(event)
            return events

        events = asyncio.run(run())
        types = [event["type"] for event in events]
        assert types == ["init", "page_error", "page_start", "page_done", "complete"]
        assert "pixmap" in events[1]["message"]
        assert events[-1]["failed_pages"] == [1]
        assert events[-1]["ok_pages"] == [2]
        assert len(holder["client"].chat.completions.calls) == 1

    def test_invalid_page_range_is_an_error_event(self):
        events, client = _collect(_pdf_bytes(2), [], page_ranges="abc")
        assert client is None
        assert events == [
            {
                "type": "error",
                "message": "Geçersiz sayfa ifadesi: 'abc'",
            }
        ]

    def test_env_limits_and_render_off_the_event_loop(self, monkeypatch):
        monkeypatch.setenv("ATLASDOC_TIMEOUT", "45")
        monkeypatch.setenv("ATLASDOC_MAX_RETRIES", "3")
        monkeypatch.setenv("ATLASDOC_MAX_TOKENS", "1000")
        seen = []

        def fake_render(pdf_bytes, page_index, dpi):
            seen.append(threading.current_thread())
            return ("aGVsbG8=", [])

        pdf = _pdf_bytes(1)
        holder = {}

        def factory(*args, **factory_kwargs):
            client = _Client([_completion("tamam")])
            client.init_kwargs = factory_kwargs
            holder["client"] = client
            return client

        async def run():
            events = []
            with patch("converter.AsyncOpenAI", factory), patch(
                "converter.render_page_from_bytes", fake_render
            ):
                async for event in process_pdf_stream(pdf, dpi=72):
                    events.append(event)
            return events

        events = asyncio.run(run())
        assert events[-1]["type"] == "complete"
        assert seen and seen[0] is not threading.main_thread()
        assert holder["client"].init_kwargs["timeout"] == 45.0
        assert holder["client"].init_kwargs["max_retries"] == 3
        assert holder["client"].chat.completions.calls[0]["max_tokens"] == 1000

    def test_invalid_env_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv("ATLASDOC_TIMEOUT", "abc")
        monkeypatch.setenv("ATLASDOC_MAX_RETRIES", "nope")
        monkeypatch.setenv("ATLASDOC_MAX_TOKENS", "")
        _events, client = _collect(_pdf_bytes(1), [_completion("x")])
        assert client.init_kwargs["timeout"] == DEFAULT_REQUEST_TIMEOUT
        assert client.init_kwargs["max_retries"] == DEFAULT_MAX_RETRIES
        assert client.chat.completions.calls[0]["max_tokens"] == DEFAULT_MAX_TOKENS

    def test_cloud_provider_requires_api_key(self):
        events, client = _collect(
            _pdf_bytes(1),
            [],
            provider="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            model_name="gemini-2.5-flash",
            api_key="",
        )
        assert client is None
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert "API Key" in events[0]["message"]

    def test_ollama_and_cloud_provider_resolution(self):
        label, url, key, req = resolve_provider_config("ollama", "http://localhost:11434", "")
        assert label == "Ollama"
        assert url == "http://localhost:11434/v1"
        assert key == "ollama"
        assert req is False

        g_label, g_url, g_key, g_req = resolve_provider_config("gemini", "", "AIzaTestKey")
        assert g_label == "Google Gemini"
        assert g_url == "https://generativelanguage.googleapis.com/v1beta/openai"
        assert g_key == "AIzaTestKey"
        assert g_req is True

    def test_health_check_rejects_missing_cloud_key(self):
        res = asyncio.run(
            check_provider_health(
                base_url="",
                model_name="gpt-4o-mini",
                provider="openai",
                api_key="",
            )
        )
        assert res["ok"] is False
        assert "API Key" in res["message"]

