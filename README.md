# AtlasDoc — Vision-First PDF to Markdown

Yerel çalışan **LM Studio** (`google/gemma-4-e4b` vb. Vision LLM modelleri) ve **PyMuPDF** kullanarak PDF dokümanlarını yüksek doğrulukla, revizyon/üstü çizili metinleri arındırarak saf **Markdown (`.md`)** formatına dönüştüren web uygulaması.

## Özellikler

- **In-Memory PDF Render (`PyMuPDF`):** Dış bağımlılık (Poppler vb.) gerektirmeden sayfaları bellekte 200 DPI PNG formatına dönüştürür.
- **Vektör Seviyesinde Üstü Çizili (Strikethrough) Temizliği:** PDF içindeki üstü çizili (silinmiş/iptal edilmiş) revizyon metinlerini görsel oluşturulmadan önce koordinat bazlı tespit edip beyazlatır; altı çizili (yeni eklenen) metinleri ve tabloları korur.
- **Sliding Window (Bağlam Koruma):** Ardışık sayfa geçişlerinde cümle ve tablo bölünmelerini önlemek için bir önceki sayfanın son 15 satırını bağlam olarak aktarır.
- **Esnek Sayfa & Aralık Seçimi:** Tüm sayfaları veya `1-5, 8, 12-15, 20-` gibi özel sayfa aralıklarını işleyebilir.
- **Gerçek Zamanlı Akış (SSE):** Sayfa önizlemesi ve üretilen Markdown çıktısı Server-Sent Events (SSE) ile anlık olarak arayüze akar (`Raw Markdown` ve `marked.js` + `mermaid.js` destekli `Live Preview`).

## Kurulum ve Çalıştırma

```bash
pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Tarayıcıdan `http://127.0.0.1:8000` adresine giderek kullanabilirsiniz.
