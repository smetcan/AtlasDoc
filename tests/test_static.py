import json
import re

from fastapi.testclient import TestClient

from app import app

LOCAL_ASSET = re.compile(
    r"""<(?:script|link|img)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)


def test_homepage_loads_only_local_assets():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "__ATLASDOC_CONFIG__" not in html
    for host in (
        "cdn.tailwindcss.com",
        "cdn.jsdelivr.net",
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        "unpkg.com",
    ):
        assert host not in html

    urls = LOCAL_ASSET.findall(html)
    assert urls
    for url in urls:
        assert url.startswith("/static/"), url
        asset = client.get(url)
        assert asset.status_code == 200, url
        assert len(asset.content) > 500, url

    config = json.loads(
        re.search(
            r'<script id="atlasdoc-config" type="application/json">(.*?)</script>',
            html,
        ).group(1)
    )
    assert config["base_url"].endswith("/v1")
    assert config["model_name"]
    assert config["provider"] == "lmstudio"


def test_config_endpoint_follows_lmstudio_env(monkeypatch):
    monkeypatch.setenv("ATLASDOC_BASE_URL", "http://host.docker.internal:1234/v1")
    monkeypatch.setenv("ATLASDOC_MODEL", "qwen-vl")
    client = TestClient(app)
    payload = client.get("/api/config").json()
    assert payload == {
        "base_url": "http://host.docker.internal:1234/v1",
        "model_name": "qwen-vl",
        "provider": "lmstudio",
    }
    html = client.get("/").text
    assert "host.docker.internal:1234" in html


def test_font_files_named_by_the_stylesheet_are_served():
    client = TestClient(app)
    css = client.get("/static/vendor/fonts.css")
    assert css.status_code == 200
    names = re.findall(r"url\(fonts/([^)]+)\)", css.text)
    assert len(names) >= 8
    for name in names:
        font = client.get(f"/static/vendor/fonts/{name}")
        assert font.status_code == 200, name
        assert font.content[:4] == b"wOF2"
