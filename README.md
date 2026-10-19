# AtlasDoc — Vision-First PDF to Markdown

Yerel çalışan (**LM Studio**, **Ollama**) veya bulut tabanlı (**Google Gemini**, **OpenAI**, **Anthropic**, **OpenRouter** vb.) Vision LLM modellerini ve **PyMuPDF** kütüphanesini kullanarak PDF dokümanlarını yüksek doğrulukla, revizyon/üstü çizili metinleri arındırarak saf **Markdown (`.md`)** formatına dönüştüren web uygulaması.

## Özellikler

- **Çoklu AI Vision Sağlayıcı Desteği:**
  - **Yerel (Local):** LM Studio (`http://localhost:1234/v1`) ve Ollama (`http://localhost:11434/v1`).
  - **Bulut (Cloud API):** Google Gemini, OpenAI, Anthropic, OpenRouter ve özel OpenAI-uyumlu uç noktalar.
- **Model Profil Yönetimi:** Arayüzdeki **Modeller** paneli üzerinden birden fazla yerel veya bulut model profili tanımlanabilir, sunucudaki mevcut modeller tek tıkla listelenebilir (`Sunucudan Modelleri Listele`), bağlantı testi yapılabilir ve üst bardaki seçici ile profiller arasında anında geçiş yapılabilir. Profiller ve API anahtarları sunucuya kaydedilmez; yalnızca tarayıcının `localStorage` alanında saklanır.
- **In-Memory PDF Render (`PyMuPDF`):** Dış bağımlılık (Poppler vb.) gerektirmeden sayfaları bellekte PNG formatına dönüştürür. Render ve üstü çizili maskeleme event loop dışında çalışır.
- **Vektör Seviyesinde Üstü Çizili (Strikethrough) Temizliği:** PDF içindeki üstü çizili (silinmiş/iptal edilmiş) revizyon metinlerini görsel oluşturulmadan önce koordinat bazlı tespit edip beyazlatır; altı çizili (yeni eklenen) metinleri ve tabloları korur. Aynı ifade yürürlükteki metinde tekrar geçebileceği için LLM çıktısından ayrıca silinmez.
- **Sliding Window (Bağlam Koruma):** Ardışık sayfa geçişlerinde cümle ve tablo bölünmelerini önlemek için bir önceki sayfanın son 15 satırını bağlam olarak aktarır.
- **Esnek Sayfa & Aralık Seçimi:** Tüm sayfaları veya `1-5, 8, 12-15, 20-` gibi özel sayfa aralıklarını işleyebilir.
- **Gerçek Zamanlı Akış (SSE):** Sayfa önizlemesi ve üretilen Markdown çıktısı Server-Sent Events (SSE) ile sayfa sayfa arayüze akar (`Raw Markdown` ve `marked` + `mermaid` destekli `Live Preview`). Önizleme DOMPurify ile sanitize edilir.
- **Kesilen ve Başarısız Sayfalar:** Token sınırında kesilen sayfalar ve zaman aşımı / render hatası yaşayan sayfalar iş sonunda listelenir. Zaman aşımı tek sayfayı düşürür, işe devam edilir.

## Model Sağlayıcı Kurulumu

Üst bardaki **Modeller** butonuna tıklayarak **AI Vision Model Profilleri** penceresinden dilediğiniz sağlayıcıyı yapılandırabilirsiniz:

### 1. LM Studio (Yerel)
1. [LM Studio](https://lmstudio.ai/) uygulamasını kurun ve görüntü destekli (vision) bir model indirin (örn. `google/gemma-4-e4b` veya `Qwen/Qwen3-VL-8B-Instruct`).
2. **Local Server** sekmesinden sunucuyu başlatın (varsayılan: `http://localhost:1234/v1`).
3. AtlasDoc **Modeller** penceresinde `LM Studio (Yerel)` profilini seçin, **Sunucudan Modelleri Listele** butonuna tıklayarak yüklü modeli seçin ve **Kaydet & Seç** deyin.

### 2. Ollama (Yerel)
1. [Ollama](https://ollama.com/) uygulamasını kurun ve bir vision modeli çekin:
   ```bash
   ollama pull gemma3:4b
   # veya
   ollama pull qwen2.5vl:7b
   ```
2. AtlasDoc **Modeller** penceresinde `Ollama (Yerel)` sağlayıcısını seçin (varsayılan adres: `http://localhost:11434/v1`). **Sunucudan Modelleri Listele** ile modeli seçip kaydedin.

### 3. Bulut API Sağlayıcıları (Gemini, OpenAI, Anthropic, OpenRouter)
1. **Modeller** penceresinde **+ Yeni Ekle** butonuna tıklayın ve ilgili sağlayıcıyı seçin:
   - **Google Gemini:** `https://generativelanguage.googleapis.com/v1beta/openai` (Örn: `gemini-2.5-flash`)
   - **OpenAI:** `https://api.openai.com/v1` (Örn: `gpt-4o-mini`, `gpt-4o`)
   - **Anthropic:** `https://api.anthropic.com/v1` (Örn: `claude-sonnet-4-20250514`)
   - **OpenRouter:** `https://openrouter.ai/api/v1` (Örn: `google/gemini-2.5-flash`)
2. **API Anahtarı** alanına sağlayıcıdan aldığınız anahtarı girin, **Test Et** ile doğrulayın ve **Kaydet & Seç** butonuna basın.

## Kurulum ve Çalıştırma

```bash
pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Tarayıcıdan `http://127.0.0.1:8000` adresine giderek kullanabilirsiniz. Sunucu yalnızca loopback arayüzüne bağlanır.

## Yapılandırma

Arayüzden değişenler: Aktif model profili (sağlayıcı, API adresi, API anahtarı, model kimliği), DPI (sunucu 72–400 aralığına sıkıştırır) ve sayfa aralığı.

Sunucu süreç ortam değişkenleri:

| Değişken | Varsayılan | Anlamı |
| --- | --- | --- |
| `ATLASDOC_MAX_UPLOAD_MB` | `100` | PDF yükleme üst sınırı (MB). `1`'den küçük değerler `1` sayılır. Sınır hem `/api/pdf-info` hem `/api/convert` için geçerlidir. |
| `ATLASDOC_TIMEOUT` | `120` | Bir sayfa isteğinin saniye cinsinden zaman aşımı. Süre dolan sayfa başarısız işaretlenir, sonraki sayfaya geçilir. |
| `ATLASDOC_MAX_RETRIES` | `1` | OpenAI istemcisinin ek deneme sayısı. `0` tek deneme demektir. SDK varsayılanı `2`'dir; timeout ile çarpılınca bir sayfa çok uzun sürebilir. |
| `ATLASDOC_MAX_TOKENS` | `8192` | Sayfa başına `max_tokens`. Yanıt `finish_reason=length` ile biterse sayfa kesilmiş olarak işaretlenir, metin yine de yazılır. |

Örnek:

```bash
ATLASDOC_TIMEOUT=180 ATLASDOC_MAX_RETRIES=0 ATLASDOC_MAX_TOKENS=4096 \
  python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

## Testler

GPU veya harici model sunucusu gerekmez. LLM çağrıları sahte istemciyle yürür.

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Lisans

AtlasDoc, [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0) ile lisanslanmıştır.

Copyright (C) 2026 Selçuk Metcan

Bu seçim, bağımlılık olan **PyMuPDF**'in AGPL-3.0 veya Artifex ticari lisansı şeklinde çift lisanslı olmasından gelir. Uygulama bir ağ hizmeti olarak çalıştığı için AGPL'in ağ kullanımı koşulları geçerlidir. Başka bir proje lisansı tercih edilirse `LICENSE` ve bu bölüm güncellenmelidir; PyMuPDF'in kendi lisansı ayrıca geçerlidir.
