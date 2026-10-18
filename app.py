import json
import os
from fastapi import FastAPI, File, Form, Request, UploadFile, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from converter import check_lm_studio_health, get_pdf_page_count, process_pdf_stream

app = FastAPI(
    title="AtlasDoc — Vision-First PDF to Markdown",
    description="Yerel LM Studio (google/gemma-4-e4b) destekli PDF -> Markdown dönüştürücü",
    version="1.1.0",
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class HealthCheckRequest(BaseModel):
    base_url: str = "http://localhost:1234/v1"
    model_name: str = "google/gemma-4-e4b"


@app.get("/")
async def index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="static/index.html bulunamadı.")
    return FileResponse(index_path)


@app.post("/api/health")
async def health_check(payload: HealthCheckRequest):
    result = await check_lm_studio_health(
        base_url=payload.base_url.strip(),
        model_name=payload.model_name.strip(),
    )
    return result


@app.post("/api/pdf-info")
async def pdf_info(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Geçerli bir .pdf dosyası seçin.")
    pdf_bytes = await file.read()
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
    base_url: str = Form("http://localhost:1234/v1"),
    model_name: str = Form("google/gemma-4-e4b"),
    dpi: int = Form(200),
    page_ranges: str = Form(""),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Lütfen geçerli bir .pdf dosyası yükleyin.")

    # DPI güvenlik sınırları (72 - 400 arası)
    safe_dpi = max(72, min(400, int(dpi)))
    pdf_bytes = await file.read()

    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Yüklenen PDF dosyası boş.")

    async def sse_event_generator():
        async for event in process_pdf_stream(
            pdf_bytes=pdf_bytes,
            base_url=base_url.strip(),
            model_name=model_name.strip(),
            dpi=safe_dpi,
            page_ranges=page_ranges.strip(),
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
