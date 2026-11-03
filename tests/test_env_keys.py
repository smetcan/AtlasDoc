"""`.env` / ortam değişkeninden gelen API anahtarlarının kullanıldığını doğrular."""

import asyncio

from converter import process_pdf_stream, provider_env_api_key, resolve_provider_config


def test_env_key_is_used_by_provider_config(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
    _, _, key, requires_key = resolve_provider_config(provider="gemini", base_url="", api_key="")
    assert requires_key is True
    assert key == "env-gemini-key"
    assert provider_env_api_key("gemini") == "env-gemini-key"


def test_ui_key_wins_over_env_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
    _, _, key, _ = resolve_provider_config(provider="gemini", base_url="", api_key="ui-key")
    assert key == "ui-key"


def test_env_key_satisfies_required_key_guard(monkeypatch):
    """Anahtar yalnızca .env'de olsa bile dönüşüm 'anahtar gerekli' hatası vermemeli."""
    monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")

    async def first_event():
        async for event in process_pdf_stream(
            b"not-a-pdf", provider="gemini", api_key="", model_name="gemini-2.5-flash"
        ):
            return event
        return None

    event = asyncio.run(first_event())
    assert event is not None
    assert event["type"] == "error"
    assert "PDF" in event["message"]
    assert "API anahtarı" not in event["message"]


def test_missing_key_still_reports_api_key_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("ATLASDOC_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ATLASDOC_API_KEY", raising=False)

    async def first_event():
        async for event in process_pdf_stream(
            b"not-a-pdf", provider="gemini", api_key="", model_name="gemini-2.5-flash"
        ):
            return event
        return None

    event = asyncio.run(first_event())
    assert event is not None
    assert "API anahtarı" in event["message"]
