import asyncio
import base64
import os
import re
from typing import AsyncGenerator, Dict, Any, List, Optional, Tuple
try:
    import pymupdf as fitz
except ImportError:
    import fitz  # PyMuPDF
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError

# SDK varsayılanı max_retries=2. Timeout ile çarpılınca bir sayfa çok uzun sürebilir.
# Burada 120 sn ve 1 ek deneme esas alınır; ikisi de ortam değişkeniyle değişir.
DEFAULT_REQUEST_TIMEOUT = 120.0
DEFAULT_MAX_RETRIES = 1
DEFAULT_MAX_TOKENS = 8192


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def resolve_llm_limits(
    timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
    max_tokens: Optional[int] = None,
) -> Tuple[float, int, int]:
    """İstek timeout'u, retry sayısı ve max_tokens. Argüman, yoksa ortam değişkeni."""
    if timeout is None:
        timeout = _env_float("ATLASDOC_TIMEOUT", DEFAULT_REQUEST_TIMEOUT)
    if max_retries is None:
        max_retries = _env_int("ATLASDOC_MAX_RETRIES", DEFAULT_MAX_RETRIES)
    if max_tokens is None:
        max_tokens = _env_int("ATLASDOC_MAX_TOKENS", DEFAULT_MAX_TOKENS)
    return max(1.0, float(timeout)), max(0, int(max_retries)), max(1, int(max_tokens))


SYSTEM_PROMPT = """Sen uzman bir doküman OCR ve Markdown dönüştürme motorusun. Görevin, verilen sayfa görselini hiçbir veriyi atlamadan ve uydurmadan birebir Markdown formatına çevirmektir.

Kesin Kurallar:
- Sayfa üst bilgilerini (header), alt bilgilerini (footer), sayfa numaralarını ve filigranları yoksay.
- ÜSTÜ ÇİZİLİ (STRIKETHROUGH) METİN KURALI: Dokümanda ortasından yatay çizgi geçen (üstü çizili) kelimeler ve cümle parçaları SİLİNMİŞ/İPTAL EDİLMİŞ metinlerdir. Bunları KESİNLİKLE YOKSAY ve çıktıya yazma.
- DİKKAT (Altı Çizili vs. Üstü Çizili Ayrımı): Aynı satırda veya aynı cümlede altı çizili (çizgi harflerin tabanında/altında) bir ifadeden hemen sonra (örneğin nokta veya virgülden sonra) üstü çizili (çizgi harflerin tam ortasında) kelimeler gelebilir. Altı çizili olanlar yürürlükteki yeni metindir, onları normal yaz; ancak çizginin yukarı çıkıp harflerin ortasını kestiği (üstü çizili) son kelimeleri kesinlikle çıktıya dahil etme.
- Tabloları eksiksiz olarak standart Markdown tablosuna çevir (tablo içinde üstü çizili silinmiş değerler varsa onları da yoksay).
- Sayfada akış şeması veya diyagram varsa bunu bir `mermaid` kod bloğu olarak ifade et veya detaylıca betimle.
- Yorum yapma, açıklama ekleme, doğrudan Markdown çıktısını ver."""


def _strip_surrounding_blank_lines(text: str) -> str:
    """Baştaki ve sondaki boş satırları alır; satır girintisine dokunmaz."""
    lines = text.splitlines()
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end])


_FENCE_OPEN_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")


def _is_fence_close(line: str, fence_char: str, fence_len: int) -> bool:
    return (
        re.match(rf"^[ ]{{0,3}}{re.escape(fence_char)}{{{fence_len},}}[ ]*$", line) is not None
    )


def _cleanup_prose(text: str) -> str:
    """
    Çit dışındaki metinde üstü çizili işaretleri, satır içi fazla boşluğu ve
    noktalama öncesi boşluğu temizler. Satır başı girintisi korunur.
    """
    text = re.sub(r"~~[^~]+?~~", "", text)
    text = re.sub(
        r"<(?:del|s|strike)\b[^>]*>[\s\S]*?</(?:del|s|strike)>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Yalnızca iki yanında metin olan boşluk dizisini daralt. Satır başı
    # girintisi ve satır sonundaki Markdown sert satır sonu (iki boşluk) kalır.
    text = re.sub(r"(?<=\S)[^\S\r\n]{2,}(?=\S)", " ", text)
    text = re.sub(r"[^\S\r\n]+([.,;:!?])", r"\1", text)
    text = re.sub(r"\.,", ".", text)
    return text


def _cleanup_outside_fences(text: str) -> str:
    """``` ve ~~~ çitlerinin içine dokunmaz; kapanmamış çiti de olduğu gibi bırakır."""
    lines = text.splitlines()
    parts: List[str] = []
    prose: List[str] = []
    code: List[str] = []
    in_code = False
    fence_char = ""
    fence_len = 0

    def flush_prose() -> None:
        nonlocal prose
        if prose:
            parts.append(_cleanup_prose("\n".join(prose)))
            prose = []

    def flush_code() -> None:
        nonlocal code
        if code:
            parts.append("\n".join(code))
            code = []

    for line in lines:
        if not in_code:
            match = _FENCE_OPEN_RE.match(line)
            if match:
                flush_prose()
                in_code = True
                token = match.group(1)
                fence_char = token[0]
                fence_len = len(token)
                code.append(line)
            else:
                prose.append(line)
            continue

        code.append(line)
        if _is_fence_close(line, fence_char, fence_len):
            in_code = False
            flush_code()

    if in_code:
        flush_code()
    else:
        flush_prose()

    return "\n".join(parts)


def clean_llm_markdown(raw_text: str) -> str:
    """
    Model çıktısındaki düşünme bloklarını (<think>...</think>, <|channel>thought...),
    en dıştaki ```markdown ... ``` sarmalayıcılarını ve ~~ / <del> işaretlerini temizler.
    Satır girintisi ile çitli kod / mermaid bloklarının içeriği korunur.
    Üstü çizili PDF metni görsel maskelemede kapatıldığı için çıktıdan ayrıca silinmez.
    """
    if not raw_text:
        return ""

    text = _strip_surrounding_blank_lines(raw_text)
    if not text:
        return ""

    # 1. <think>...</think> veya <thought>...</thought> bloklarını temizle
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<thought>[\s\S]*?</thought>", "", text, flags=re.IGNORECASE)

    # 2. Gemma-4 / LM Studio özel kanal düşünme bloklarını temizle
    text = re.sub(
        r"<\|?channel\|?>\s*thought[\s\S]*?(?:<\|?channel\|?>|<\|?message\|?>|<\|?end\|?>)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = _strip_surrounding_blank_lines(text)

    if text.lower().startswith("<think>") and "</think>" not in text.lower():
        text = re.sub(r"^<think>", "", text, flags=re.IGNORECASE)
        text = _strip_surrounding_blank_lines(text)

    # 3. En dıştaki ```markdown ... ``` veya ```md ... ``` sarmalayıcısını kaldır
    outer_fence_pattern = re.compile(
        r"^(`{3,4})(?:markdown|md)\s*\n([\s\S]*?)\n\1\s*$",
        re.IGNORECASE,
    )
    match = outer_fence_pattern.match(text)
    if match:
        fence = match.group(1)
        inner_content = match.group(2)
        if fence not in inner_content:
            text = _strip_surrounding_blank_lines(inner_content)
    else:
        if re.match(r"^`{3,4}(?:markdown|md)\s*\n", text, re.IGNORECASE) and re.search(
            r"\n`{3,4}\s*$", text
        ):
            text = re.sub(r"^`{3,4}(?:markdown|md)\s*\n", "", text, count=1, flags=re.IGNORECASE)
            text = re.sub(r"\n`{3,4}\s*$", "", text, count=1)
            text = _strip_surrounding_blank_lines(text)

    # 4. Çit dışındaki ~~ / <del> işaretleri ve satır içi boşluklar
    return _strip_surrounding_blank_lines(_cleanup_outside_fences(text))


def get_last_n_lines(text: str, n: int = 15) -> str:
    """Metnin son N satırını döndürür (Sliding Window bağlamı için)."""
    if not text or not text.strip():
        return ""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-n:])


def parse_page_ranges(range_str: str, total_pages: int) -> List[int]:
    """
    Kullanıcının girdiği sayfa/sayfa aralığı metnini (Örn: '1-5, 8, 11-14, 20-')
    1-tabanlı sıralı ve tekil sayfa numaraları listesine dönüştürür.
    Boş bırakılırsa tüm sayfaları (1..total_pages) döndürür.
    """
    if not range_str or not range_str.strip() or range_str.strip().lower() in ("all", "tümü", "*"):
        return list(range(1, total_pages + 1))

    selected_pages = set()
    parts = [p.strip() for p in range_str.split(",") if p.strip()]

    for part in parts:
        if "-" in part:
            bounds = part.split("-", 1)
            start_s, end_s = bounds[0].strip(), bounds[1].strip()

            start_p = int(start_s) if start_s.isdigit() else 1
            end_p = int(end_s) if end_s.isdigit() else total_pages

            if start_p > end_p:
                start_p, end_p = end_p, start_p

            start_p = max(1, min(total_pages, start_p))
            end_p = max(1, min(total_pages, end_p))

            for p in range(start_p, end_p + 1):
                selected_pages.add(p)
        else:
            if not part.isdigit():
                raise ValueError(f"Geçersiz sayfa ifadesi: '{part}'")
            p = int(part)
            if 1 <= p <= total_pages:
                selected_pages.add(p)

    result = sorted(selected_pages)
    if not result:
        raise ValueError(f"Belirtilen aralık ({range_str}) 1-{total_pages} sayfa sınırları dışında kaldı.")
    return result


def get_pdf_page_count(pdf_bytes: bytes) -> int:
    """PDF dosyasının toplam sayfa sayısını bellekte okuyarak döndürür."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return len(doc)


def mask_strikethrough_on_page(page: fitz.Page) -> List[str]:
    """
    PyMuPDF ile sayfadaki yatay vektör çizgilerini ve StrikeOut anotasyonlarını inceler.
    Karakterlerin tam ortasından (%28 - %72 yükseklik bandı) geçen üstü çizili (strikethrough)
    metinleri tespit eder, bunları PNG render öncesinde beyaz dikdörtgenle fiziksel olarak
    maskeler (siler) ve silinen metin öbeklerinin listesini döndürür.
    Altı çizili (underline > %75 yükseklik) metinlere ve tablo çizgilerine dokunmaz.
    """
    deleted_phrases: List[str] = []
    horizontal_segments: List[tuple] = []

    # 1. PDF StrikeOut anotasyonlarını topla
    try:
        for annot in page.annots() or []:
            if annot.type[0] == fitz.PDF_ANNOT_STRIKE_OUT:
                r = annot.rect
                y_mid = 0.5 * (r.y0 + r.y1)
                horizontal_segments.append((r.x0, y_mid, r.x1, r.y0, r.y1))
    except Exception:
        pass

    # 2. Sayfadaki tüm vektör çizimlerinden yatay çizgi parçalarını çıkar
    try:
        for d in page.get_drawings():
            d_rect = d.get("rect")
            if d_rect and d_rect.height <= 3.6 and d_rect.width >= 3.0:
                y_mid = 0.5 * (d_rect.y0 + d_rect.y1)
                horizontal_segments.append((d_rect.x0, y_mid, d_rect.x1, d_rect.y0, d_rect.y1))

            for item in d.get("items", []):
                kind = item[0]
                if kind == "l":
                    p1, p2 = item[1], item[2]
                    if abs(p1.y - p2.y) <= 1.5 and abs(p2.x - p1.x) >= 3.0:
                        x0, x1 = min(p1.x, p2.x), max(p1.x, p2.x)
                        y0, y1 = min(p1.y, p2.y), max(p1.y, p2.y)
                        horizontal_segments.append((x0, 0.5 * (y0 + y1), x1, y0 - 0.8, y1 + 0.8))
                elif kind == "re":
                    r = item[1]
                    if r.height <= 3.6 and r.width >= 3.0:
                        horizontal_segments.append((r.x0, 0.5 * (r.y0 + r.y1), r.x1, r.y0, r.y1))
                elif kind == "qu":
                    r = item[1].rect
                    if r.height <= 3.6 and r.width >= 3.0:
                        horizontal_segments.append((r.x0, 0.5 * (r.y0 + r.y1), r.x1, r.y0, r.y1))
    except Exception:
        pass

    if not horizontal_segments:
        return deleted_phrases

    # 3. Sayfadaki karakterleri (rawdict) incele ve ortasından çizgi geçenleri bul
    rects_to_whiteout: List[fitz.Rect] = []
    strike_segments_to_hide: List[fitz.Rect] = []

    try:
        raw = page.get_text("rawdict")
        for block in raw.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_chars = []
                for span in line.get("spans", []):
                    for ch in span.get("chars", []):
                        line_chars.append(ch)

                if not line_chars:
                    continue

                current_deleted_run: List[str] = []
                for ch in line_chars:
                    cx0, cy0, cx1, cy1 = ch["bbox"]
                    ch_h = cy1 - cy0
                    if ch_h <= 1.0:
                        continue
                    cx_mid = 0.5 * (cx0 + cx1)

                    is_struck = False
                    for sx0, sy_mid, sx1, sy0, sy1 in horizontal_segments:
                        if (sx0 - 1.5) <= cx_mid <= (sx1 + 1.5):
                            rel_y = (sy_mid - cy0) / ch_h
                            if 0.28 <= rel_y <= 0.72:
                                is_struck = True
                                strike_segments_to_hide.append(
                                    fitz.Rect(sx0 - 0.5, sy0 - 0.8, sx1 + 0.5, sy1 + 0.8)
                                )
                                break

                    if is_struck:
                        current_deleted_run.append(ch.get("c", ""))
                        rects_to_whiteout.append(
                            fitz.Rect(cx0 - 0.3, cy0 + 0.06 * ch_h, cx1 + 0.3, cy1 - 0.04 * ch_h)
                        )
                    else:
                        if current_deleted_run:
                            phrase = "".join(current_deleted_run).strip()
                            if len(phrase) >= 2:
                                deleted_phrases.append(phrase)
                            current_deleted_run = []

                if current_deleted_run:
                    phrase = "".join(current_deleted_run).strip()
                    if len(phrase) >= 2:
                        deleted_phrases.append(phrase)
    except Exception:
        pass

    # 4. Tespit edilen üstü çizili karakterleri ve çizgileri bellekte beyaz dikdörtgenle kapat
    if rects_to_whiteout or strike_segments_to_hide:
        shape = page.new_shape()
        for r in rects_to_whiteout + strike_segments_to_hide:
            shape.draw_rect(r)
        shape.finish(color=(1, 1, 1), fill=(1, 1, 1), width=0)
        shape.commit()

    return deleted_phrases


def render_page_to_base64(
    doc: fitz.Document, page_index: int, dpi: int = 200
) -> tuple[str, List[str]]:
    """
    PyMuPDF kullanarak belirtilen sayfada önce üstü çizili (silinmiş) metinleri
    bellekte beyazlatır, ardından sayfayı diske kaydetmeden (in-memory)
    PNG formatına ve Base64 string'e dönüştürür.
    """
    page = doc.load_page(page_index)
    deleted_phrases = mask_strikethrough_on_page(page)
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    png_bytes = pix.tobytes("png")
    return base64.b64encode(png_bytes).decode("utf-8"), deleted_phrases


def render_page_from_bytes(
    pdf_bytes: bytes, page_index: int, dpi: int = 200
) -> tuple[str, List[str]]:
    """
    Sayfayı iş parçacığında render etmek için belgeyi bu çağrıda açar.
    PyMuPDF Document nesnesi iş parçacıkları arasında paylaşılmaz.
    """
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return render_page_to_base64(doc, page_index, dpi=dpi)


async def check_lm_studio_health(base_url: str, model_name: str) -> Dict[str, Any]:
    """LM Studio yerel sunucusuna bağlantıyı ve model durumunu test eder."""
    client = AsyncOpenAI(base_url=base_url.rstrip("/"), api_key="lm-studio", timeout=8.0)
    try:
        models_resp = await client.models.list()
        available_models = [m.id for m in models_resp.data]
        model_found = any(model_name.lower() in m.lower() for m in available_models)
        return {
            "ok": True,
            "available_models": available_models,
            "model_found": model_found,
            "message": (
                f"Bağlantı başarılı! '{model_name}' hazır."
                if model_found
                else f"LM Studio aktif, ancak '{model_name}' listede görünmüyor. Yüklü modeller: {', '.join(available_models) or 'Yok'}"
            ),
        }
    except APIConnectionError:
        return {
            "ok": False,
            "available_models": [],
            "model_found": False,
            "message": f"LM Studio sunucusuna ({base_url}) ulaşılamadı. Local Server'ın açık olduğundan emin olun.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "available_models": [],
            "model_found": False,
            "message": f"Bağlantı hatası: {str(exc)}",
        }


async def process_pdf_stream(
    pdf_bytes: bytes,
    base_url: str = "http://localhost:1234/v1",
    model_name: str = "google/gemma-4-e4b",
    dpi: int = 200,
    page_ranges: str = "",
    timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
    max_tokens: Optional[int] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    PDF'i seçilen sayfa/sayfa aralığına göre bellekte PNG'ye çevirir,
    ardışık sayfalarda Sliding Window bağlamıyla Vision LLM'e gönderir
    ve her adımı SSE (Server-Sent Events) için yield eder.
    """
    timeout, max_retries, max_tokens = resolve_llm_limits(timeout, max_retries, max_tokens)

    try:
        doc_total_pages = await asyncio.to_thread(get_pdf_page_count, pdf_bytes)
    except Exception as exc:
        yield {"type": "error", "message": f"PDF dosyası açılamadı veya bozuk: {str(exc)}"}
        return

    if doc_total_pages == 0:
        yield {"type": "error", "message": "PDF dosyasında işlenecek sayfa bulunamadı."}
        return

    try:
        target_pages = parse_page_ranges(page_ranges, doc_total_pages)
    except ValueError as val_exc:
        yield {"type": "error", "message": str(val_exc)}
        return

    selected_count = len(target_pages)

    yield {
        "type": "init",
        "total_pages": selected_count,
        "doc_total_pages": doc_total_pages,
        "target_pages": target_pages,
        "dpi": dpi,
        "model": model_name,
    }

    client = AsyncOpenAI(
        base_url=base_url.rstrip("/"),
        api_key="lm-studio",
        timeout=timeout,
        max_retries=max_retries,
    )

    previous_page_markdown: str = ""
    previous_page_num: Optional[int] = None
    ok_pages: List[int] = []
    failed_pages: List[int] = []
    truncated_pages: List[int] = []

    for step_idx, page_num in enumerate(target_pages, start=1):
        page_index = page_num - 1

        # 1. Maskeleme ve PNG render event loop dışında çalışır
        try:
            img_b64, _deleted_phrases = await asyncio.to_thread(
                render_page_from_bytes, pdf_bytes, page_index, dpi
            )
        except Exception as render_exc:
            failed_pages.append(page_num)
            yield {
                "type": "page_error",
                "page": page_num,
                "step": step_idx,
                "total_pages": selected_count,
                "message": f"Sayfa {page_num} görsele dönüştürülemedi: {str(render_exc)}",
            }
            continue

        # Arayüze o an işlenen sayfanın küçük görselini ve durumunu bildir
        yield {
            "type": "page_start",
            "page": page_num,
            "step": step_idx,
            "total_pages": selected_count,
            "doc_total_pages": doc_total_pages,
            "image_base64": img_b64,
        }

        # 2. Sliding Window (Bağlam Koruma)
        is_consecutive = (previous_page_num is not None) and (previous_page_num == page_num - 1)
        context_tail = get_last_n_lines(previous_page_markdown, n=15) if is_consecutive else ""

        if context_tail:
            user_text = (
                f"Önceki sayfanın sonu (sadece bağlam için, bunu tekrar yazma):\n"
                f"---\n{context_tail}\n---\n\n"
                f"Şimdi ekteki {page_num}. sayfa görselini (önceki sayfadaki yarım kalan cümle veya tablo varsa devamlılığını koruyarak) "
                f"birebir Markdown formatına çevir."
            )
        else:
            user_text = (
                f"Ekteki {page_num}. sayfa görselini hiçbir veriyi atlamadan birebir Markdown formatına çevir."
            )

        # 3. Vision LLM İsteği (Temperature = 0.1)
        try:
            response = await client.chat.completions.create(
                model=model_name,
                temperature=0.1,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_b64}"
                                },
                            },
                        ],
                    },
                ],
            )
            if not response.choices:
                failed_pages.append(page_num)
                yield {
                    "type": "page_error",
                    "page": page_num,
                    "step": step_idx,
                    "total_pages": selected_count,
                    "message": f"Sayfa {page_num} için model boş yanıt döndürdü.",
                }
                continue

            choice = response.choices[0]
            raw_output = choice.message.content or ""
            finish_reason = getattr(choice, "finish_reason", None)
            truncated = finish_reason == "length"
            cleaned_md = clean_llm_markdown(raw_output)
            previous_page_markdown = cleaned_md
            previous_page_num = page_num
            ok_pages.append(page_num)
            if truncated:
                truncated_pages.append(page_num)

            yield {
                "type": "page_done",
                "page": page_num,
                "step": step_idx,
                "total_pages": selected_count,
                "doc_total_pages": doc_total_pages,
                "markdown": cleaned_md,
                "truncated": truncated,
                "finish_reason": finish_reason,
            }

        except APITimeoutError:
            # APITimeoutError, APIConnectionError alt sınıfıdır; bu dal önce gelmeli.
            failed_pages.append(page_num)
            yield {
                "type": "page_error",
                "page": page_num,
                "step": step_idx,
                "total_pages": selected_count,
                "message": f"Sayfa {page_num} zaman aşımına uğradı ({timeout:g} sn).",
            }
        except APIConnectionError:
            failed_pages.append(page_num)
            yield {
                "type": "error",
                "page": page_num,
                "message": f"Sayfa {page_num} işlenirken LM Studio bağlantısı koptu ({base_url}).",
            }
            return
        except APIStatusError as status_exc:
            failed_pages.append(page_num)
            yield {
                "type": "error",
                "page": page_num,
                "message": f"LM Studio API Hatası ({status_exc.status_code}): {status_exc.message}",
            }
            return
        except Exception as llm_exc:
            failed_pages.append(page_num)
            yield {
                "type": "error",
                "page": page_num,
                "message": f"Sayfa {page_num} LLM hatası: {str(llm_exc)}",
            }
            return

    yield {
        "type": "complete",
        "total_pages": selected_count,
        "doc_total_pages": doc_total_pages,
        "ok_pages": ok_pages,
        "failed_pages": failed_pages,
        "truncated_pages": truncated_pages,
    }
