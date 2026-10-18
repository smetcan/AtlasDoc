import base64
import re
from typing import AsyncGenerator, Dict, Any, List, Optional
try:
    import pymupdf as fitz
except ImportError:
    import fitz  # PyMuPDF
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError

SYSTEM_PROMPT = """Sen uzman bir doküman OCR ve Markdown dönüştürme motorusun. Görevin, verilen sayfa görselini hiçbir veriyi atlamadan ve uydurmadan birebir Markdown formatına çevirmektir.

Kesin Kurallar:
- Sayfa üst bilgilerini (header), alt bilgilerini (footer), sayfa numaralarını ve filigranları yoksay.
- ÜSTÜ ÇİZİLİ (STRIKETHROUGH) METİN KURALI: Dokümanda ortasından yatay çizgi geçen (üstü çizili) kelimeler ve cümle parçaları SİLİNMİŞ/İPTAL EDİLMİŞ metinlerdir. Bunları KESİNLİKLE YOKSAY ve çıktıya yazma.
- DİKKAT (Altı Çizili vs. Üstü Çizili Ayrımı): Aynı satırda veya aynı cümlede altı çizili (çizgi harflerin tabanında/altında) bir ifadeden hemen sonra (örneğin nokta veya virgülden sonra) üstü çizili (çizgi harflerin tam ortasında) kelimeler gelebilir. Altı çizili olanlar yürürlükteki yeni metindir, onları normal yaz; ancak çizginin yukarı çıkıp harflerin ortasını kestiği (üstü çizili) son kelimeleri kesinlikle çıktıya dahil etme.
- Tabloları eksiksiz olarak standart Markdown tablosuna çevir (tablo içinde üstü çizili silinmiş değerler varsa onları da yoksay).
- Sayfada akış şeması veya diyagram varsa bunu bir `mermaid` kod bloğu olarak ifade et veya detaylıca betimle.
- Yorum yapma, açıklama ekleme, doğrudan Markdown çıktısını ver."""


def clean_llm_markdown(raw_text: str, deleted_phrases: Optional[List[str]] = None) -> str:
    """
    Model çıktısındaki düşünme bloklarını (<think>...</think>, <|channel>thought...),
    en dıştaki ```markdown ... ``` sarmalayıcılarını, üstü çizili (~~...~~, <del>...</del>)
    işaretlerini ve PDF vektör analizinden tespit edilen silinmiş ifadeleri temizler.
    """
    if not raw_text:
        return ""

    text = raw_text.strip()

    # 1. <think>...</think> veya <thought>...</thought> bloklarını temizle
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"<thought>[\s\S]*?</thought>", "", text, flags=re.IGNORECASE).strip()

    # 2. Gemma-4 / LM Studio özel kanal düşünme bloklarını temizle
    text = re.sub(
        r"<\|?channel\|?>\s*thought[\s\S]*?(?:<\|?channel\|?>|<\|?message\|?>|<\|?end\|?>)",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    if text.lower().startswith("<think>") and "</think>" not in text.lower():
        text = re.sub(r"^<think>", "", text, flags=re.IGNORECASE).strip()

    # 3. En dıştaki ```markdown ... ``` veya ```md ... ``` sarmalayıcısını kaldır
    outer_fence_pattern = re.compile(
        r"^(`{3,4})(?:markdown|md)?\s*\r?\n([\s\S]*?)\r?\n\1\s*$",
        re.IGNORECASE,
    )
    match = outer_fence_pattern.match(text)
    if match:
        fence = match.group(1)
        inner_content = match.group(2)
        if fence not in inner_content:
            text = inner_content.strip()
    else:
        if re.match(r"^`{3,4}(?:markdown|md)\s*\r?\n", text, re.IGNORECASE) and re.search(
            r"\r?\n`{3,4}\s*$", text
        ):
            text = re.sub(r"^`{3,4}(?:markdown|md)\s*\r?\n", "", text, count=1, flags=re.IGNORECASE)
            text = re.sub(r"\r?\n`{3,4}\s*$", "", text, count=1)
            text = text.strip()

    # 4. Modelin ~~...~~, <del>...</del>, <s>...</s> olarak işaretlediği silinmiş metinleri kaldır
    text = re.sub(r"~~[^~]+?~~", "", text)
    text = re.sub(r"<(?:del|s|strike)\b[^>]*>[\s\S]*?</(?:del|s|strike)>", "", text, flags=re.IGNORECASE)

    # 5. PDF katmanında tespit edilen silinmiş (üstü çizili) cümle parçaları varsa metinden temizle
    if deleted_phrases:
        for phrase in sorted(set(deleted_phrases), key=len, reverse=True):
            clean_p = phrase.strip()
            if len(clean_p) >= 3:
                escaped = r"\s+".join(re.escape(tok) for tok in clean_p.split())
                text = re.sub(escaped, "", text, flags=re.IGNORECASE)

    # Silme sonrası satır içinde oluşabilecek çift boşlukları ve noktalama hatalarını düzelt
    text = re.sub(r"([^\S\r\n]{2,})", lambda m: " " if "\n" not in m.group(0) else m.group(0), text)
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    text = re.sub(r"\.,", ".", text)

    return text


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
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    PDF'i seçilen sayfa/sayfa aralığına göre bellekte PNG'ye çevirir,
    ardışık sayfalarda Sliding Window bağlamıyla Vision LLM'e gönderir
    ve her adımı SSE (Server-Sent Events) için yield eder.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        yield {"type": "error", "message": f"PDF dosyası açılamadı veya bozuk: {str(exc)}"}
        return

    doc_total_pages = len(doc)
    if doc_total_pages == 0:
        doc.close()
        yield {"type": "error", "message": "PDF dosyasında işlenecek sayfa bulunamadı."}
        return

    try:
        target_pages = parse_page_ranges(page_ranges, doc_total_pages)
    except ValueError as val_exc:
        doc.close()
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
        timeout=300.0,
    )

    previous_page_markdown: str = ""
    previous_page_num: Optional[int] = None

    try:
        for step_idx, page_num in enumerate(target_pages, start=1):
            page_index = page_num - 1

            # 1. Sayfayı bellekte üstü çizilileri maskeleyerek 200 DPI PNG -> Base64'e çevir
            try:
                img_b64, deleted_phrases = render_page_to_base64(doc, page_index, dpi=dpi)
            except Exception as render_exc:
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
                raw_output = response.choices[0].message.content or ""
                cleaned_md = clean_llm_markdown(raw_output, deleted_phrases=deleted_phrases)
                previous_page_markdown = cleaned_md
                previous_page_num = page_num

                yield {
                    "type": "page_done",
                    "page": page_num,
                    "step": step_idx,
                    "total_pages": selected_count,
                    "doc_total_pages": doc_total_pages,
                    "markdown": cleaned_md,
                }

            except APIConnectionError:
                yield {
                    "type": "error",
                    "message": f"Sayfa {page_num} işlenirken LM Studio bağlantısı koptu ({base_url}).",
                }
                return
            except APITimeoutError:
                yield {
                    "type": "page_error",
                    "page": page_num,
                    "step": step_idx,
                    "total_pages": selected_count,
                    "message": f"Sayfa {page_num} zaman aşımına uğradı (300 sn).",
                }
            except APIStatusError as status_exc:
                yield {
                    "type": "error",
                    "message": f"LM Studio API Hatası ({status_exc.status_code}): {status_exc.message}",
                }
                return
            except Exception as llm_exc:
                yield {
                    "type": "error",
                    "message": f"Sayfa {page_num} LLM hatası: {str(llm_exc)}",
                }
                return

        yield {
            "type": "complete",
            "total_pages": selected_count,
            "doc_total_pages": doc_total_pages,
        }
    finally:
        doc.close()
