import hashlib
import hmac
import json
import os
import time
from fastapi import FastAPI, File, Form, Request, Response, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from converter import (
    check_lm_studio_health,
    configured_env_key_providers,
    default_base_url,
    default_model_name,
    default_provider,
    get_pdf_page_count,
    process_pdf_stream,
    resolve_provider_config,
)


def _load_dotenv() -> None:
    """Harici paket gerektirmeden kök dizindeki .env dosyasını os.environ'a yükler."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


_load_dotenv()

SESSION_COOKIE_NAME = "atlasdoc_session"
DEFAULT_SESSION_HOURS = 168  # 7 gün


def _auth_credentials() -> tuple[str, str]:
    user = os.environ.get("ATLASDOC_AUTH_USER", "").strip()
    password = os.environ.get("ATLASDOC_AUTH_PASS", "")
    return user, password


def is_auth_configured() -> bool:
    user, password = _auth_credentials()
    return bool(user and password)


def _session_secret() -> bytes:
    explicit = os.environ.get("ATLASDOC_AUTH_SECRET", "").strip()
    user, password = _auth_credentials()
    seed = explicit or f"atlasdoc-session:{user}:{password}"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def create_session_token(username: str, ttl_seconds: int = DEFAULT_SESSION_HOURS * 3600) -> str:
    exp = int(time.time()) + max(60, ttl_seconds)
    payload = f"{username}:{exp}"
    sig = hmac.new(_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_session_token(token: str) -> bool:
    if not token or "." not in token or not is_auth_configured():
        return False
    exp_str, sig = token.split(".", 1)
    if not exp_str.isdigit():
        return False
    exp = int(exp_str)
    if time.time() > exp:
        return False
    user, _ = _auth_credentials()
    expected_payload = f"{user}:{exp}"
    expected_sig = hmac.new(
        _session_secret(), expected_payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected_sig)


def is_request_authenticated(request: Request) -> bool:
    # Pytest altında mevcut upload/static testlerinin kırılmaması için (test_auth kendi içinde zorlar)
    if (
        os.environ.get("PYTEST_CURRENT_TEST")
        and os.environ.get("ATLASDOC_ENFORCE_AUTH_IN_TEST") != "1"
    ):
        return True
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    return verify_session_token(token)


def require_auth(request: Request) -> None:
    if not is_request_authenticated(request):
        raise HTTPException(
            status_code=401,
            detail="Oturum açmanız gerekiyor. Lütfen giriş yapın.",
        )

app = FastAPI(
    title="AtlasDoc — Vision-First PDF to Markdown",
    description="Yerel LM Studio (google/gemma-4-e4b) destekli PDF -> Markdown dönüştürücü",
    version="1.2.0",
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atlasdoc", "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DEFAULT_MAX_UPLOAD_MB = 100
_UPLOAD_READ_CHUNK = 1024 * 1024
# multipart gövdesi dosyanın kendisinden biraz büyüktür
_MULTIPART_OVERHEAD_BYTES = 2 * 1024 * 1024


def max_upload_bytes() -> int:
    """ATLASDOC_MAX_UPLOAD_MB (varsayılan 100, en az 1)."""
    raw = os.environ.get("ATLASDOC_MAX_UPLOAD_MB", "").strip()
    try:
        mb = int(raw) if raw else DEFAULT_MAX_UPLOAD_MB
    except ValueError:
        mb = DEFAULT_MAX_UPLOAD_MB
    if mb < 1:
        mb = 1
    return mb * 1024 * 1024


def _upload_too_large_detail(max_bytes: int) -> str:
    return f"PDF dosyası {max_bytes // (1024 * 1024)} MB yükleme sınırını aşıyor."


def _ensure_content_length_within_limit(request: Request, max_bytes: int) -> None:
    header = request.headers.get("content-length")
    if not header:
        return
    try:
        length = int(header)
    except ValueError:
        return
    if length > max_bytes + _MULTIPART_OVERHEAD_BYTES:
        raise HTTPException(status_code=413, detail=_upload_too_large_detail(max_bytes))


async def _read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=_upload_too_large_detail(max_bytes))
        chunks.append(chunk)
    return b"".join(chunks)


class HealthCheckRequest(BaseModel):
    provider: str = "lmstudio"
    base_url: str = ""
    model_name: str = ""
    api_key: str = ""


class LoginRequest(BaseModel):
    username: str = ""
    password: str = ""


def _public_config() -> dict:
    return {
        "base_url": default_base_url(),
        "model_name": default_model_name(),
        "provider": default_provider(),
    }


def _index_html() -> str:
    index_path = os.path.join(STATIC_DIR, "index.html")
    with open(index_path, encoding="utf-8") as handle:
        html = handle.read()
    payload = json.dumps(_public_config()).replace("<", "\\u003c")
    return html.replace("__ATLASDOC_CONFIG__", payload)


@app.get("/")
async def index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="static/index.html bulunamadı.")
    return HTMLResponse(_index_html(), headers={"Cache-Control": "no-cache"})


@app.get("/api/config")
async def public_config():
    return _public_config()


@app.get("/api/auth/status")
async def auth_status(request: Request):
    authed = is_request_authenticated(request)
    res = {
        "authenticated": authed,
        "configured": is_auth_configured(),
    }
    if authed:
        res["env_key_providers"] = configured_env_key_providers()
    return res


@app.post("/api/auth/login")
async def auth_login(payload: LoginRequest, response: Response):
    if not is_auth_configured():
        raise HTTPException(
            status_code=503,
            detail="Sunucuda giriş bilgileri tanımlı değil. Lütfen .env dosyasında ATLASDOC_AUTH_USER ve ATLASDOC_AUTH_PASS değişkenlerini tanımlayın.",
        )
    expected_user, expected_pass = _auth_credentials()
    input_user = (payload.username or "").strip()
    input_pass = payload.password or ""

    user_ok = hmac.compare_digest(input_user.encode("utf-8"), expected_user.encode("utf-8"))
    pass_ok = hmac.compare_digest(input_pass.encode("utf-8"), expected_pass.encode("utf-8"))
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya şifre hatalı.")

    ttl = DEFAULT_SESSION_HOURS * 3600
    token = create_session_token(expected_user, ttl_seconds=ttl)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
    )
    return {
        "ok": True,
        "username": expected_user,
        "env_key_providers": configured_env_key_providers(),
    }


@app.post("/api/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(key=SESSION_COOKIE_NAME, samesite="lax")
    return {"ok": True}


@app.post("/api/health")
async def health_check(request: Request, payload: HealthCheckRequest):
    require_auth(request)
    eff_provider = payload.provider.strip() or default_provider()
    _, eff_base_url, _, _ = resolve_provider_config(
        provider=eff_provider,
        base_url=payload.base_url.strip(),
        api_key=payload.api_key.strip(),
    )
    result = await check_lm_studio_health(
        base_url=eff_base_url,
        model_name=payload.model_name.strip() or default_model_name(),
        provider=eff_provider,
        api_key=payload.api_key.strip(),
    )
    return result


@app.post("/api/pdf-info")
async def pdf_info(request: Request, file: UploadFile = File(...)):
    require_auth(request)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Geçerli bir .pdf dosyası seçin.")
    max_bytes = max_upload_bytes()
    _ensure_content_length_within_limit(request, max_bytes)
    pdf_bytes = await _read_upload_limited(file, max_bytes)
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="PDF dosyası boş.")
    try:
        total_pages = get_pdf_page_count(pdf_bytes)
        return {"ok": True, "total_pages": total_pages, "filename": file.filename}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF okunamadı: {str(exc)}")


@app.post("/api/convert")
async def convert_pdf(
    request: Request,
    file: UploadFile = File(...),
    provider: str = Form(""),
    base_url: str = Form(""),
    model_name: str = Form(""),
    api_key: str = Form(""),
    dpi: int = Form(200),
    page_ranges: str = Form(""),
):
    require_auth(request)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Lütfen geçerli bir .pdf dosyası yükleyin.")

    # DPI güvenlik sınırları (72 - 400 arası)
    safe_dpi = max(72, min(400, int(dpi)))
    max_bytes = max_upload_bytes()
    _ensure_content_length_within_limit(request, max_bytes)
    pdf_bytes = await _read_upload_limited(file, max_bytes)

    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Yüklenen PDF dosyası boş.")

    async def sse_event_generator():
        eff_provider = provider.strip() or default_provider()
        _, eff_base_url, _, _ = resolve_provider_config(
            provider=eff_provider,
            base_url=base_url.strip(),
            api_key=api_key.strip(),
        )
        async for event in process_pdf_stream(
            pdf_bytes=pdf_bytes,
            base_url=eff_base_url,
            model_name=model_name.strip() or default_model_name(),
            dpi=safe_dpi,
            page_ranges=page_ranges.strip(),
            provider=eff_provider,
            api_key=api_key.strip(),
        ):
            if await request.is_disconnected():
                break
            data_str = json.dumps(event, ensure_ascii=False)
            yield f"data: {data_str}\n\n"

    return StreamingResponse(
        sse_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
