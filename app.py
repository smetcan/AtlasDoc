import json
import os
from fastapi import FastAPI, File, Form, Request, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from converter import (
    check_lm_studio_health,
    default_base_url,
    default_model_name,
    default_provider,
    get_pdf_page_count,
    process_pdf_stream,
    resolve_provider_config,
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


@app.post("/api/health")
async def health_check(payload: HealthCheckRequest):
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
