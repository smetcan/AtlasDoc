# AtlasDoc — Vision-First PDF to Markdown

Yerel çalışan **LM Studio** (vision LLM) ve **PyMuPDF** kullanarak PDF dokümanlarını yüksek doğrulukla, revizyon/üstü çizili metinleri arındırarak saf **Markdown (`.md`)** formatına dönüştüren web uygulaması.

## Özellikler

- **In-Memory PDF Render (`PyMuPDF`):** Dış bağımlılık (Poppler vb.) gerektirmeden sayfaları bellekte PNG formatına dönüştürür. Render ve üstü çizili maskeleme event loop dışında çalışır.
- **Vektör Seviyesinde Üstü Çizili (Strikethrough) Temizliği:** PDF içindeki üstü çizili (silinmiş/iptal edilmiş) revizyon metinlerini görsel oluşturulmadan önce koordinat bazlı tespit edip beyazlatır; altı çizili (yeni eklenen) metinleri ve tabloları korur. Aynı ifade yürürlükteki metinde tekrar geçebileceği için LLM çıktısından ayrıca silinmez.
- **Sliding Window (Bağlam Koruma):** Ardışık sayfa geçişlerinde cümle ve tablo bölünmelerini önlemek için bir önceki sayfanın son 15 satırını bağlam olarak aktarır.
- **Esnek Sayfa & Aralık Seçimi:** Tüm sayfaları veya `1-5, 8, 12-15, 20-` gibi özel sayfa aralıklarını işleyebilir.
- **Gerçek Zamanlı Akış (SSE):** Sayfa önizlemesi ve üretilen Markdown çıktısı Server-Sent Events (SSE) ile sayfa sayfa arayüze akar (`Raw Markdown` ve `marked` + `mermaid` destekli `Live Preview`). Önizleme DOMPurify ile sanitize edilir.
- **Kesilen ve başarısız sayfalar:** Token sınırında kesilen sayfalar ve zaman aşımı / render hatası yaşayan sayfalar iş sonunda listelenir. Zaman aşımı tek sayfayı düşürür, işe devam edilir.

## LM Studio kurulumu

1. [LM Studio](https://lmstudio.ai/) uygulamasını kurun.
2. Bir **vision** (görüntü girdisi olan) model indirin. Bu projenin arayüzdeki varsayılan adı `google/gemma-4-e4b` şeklindedir. LM Studio'da görünen kimlik kurulumdan kuruluma değişir; Hugging Face karşılığı `google/gemma-4-E4B-it`, GGUF paketi ise çoğunlukla `lmstudio-community/gemma-4-E4B-it-GGUF` altındadır. Belge ve tablo için `Qwen/Qwen3-VL-8B-Instruct` (`lmstudio-community/Qwen3-VL-8B-Instruct-GGUF`) de uygun bir adaydır.
3. LM Studio'da **Local Server** (Developer sekmesi) bölümünü açın ve sunucuyu başlatın. Varsayılan adres `http://localhost:1234` olur; OpenAI uyumlu uç `http://localhost:1234/v1` şeklindedir.
4. Yüklü modelin kimliğini sunucu arayüzünden veya `GET http://localhost:1234/v1/models` yanıtındaki `id` alanından kopyalayın. AtlasDoc sağlık kontrolü bu kimliği alt dizgi olarak arar; convert isteği ise yazdığınız kimliği olduğu gibi gönderir. Kimlik listede yoksa dönüşüm 404 alabilir.
5. AtlasDoc arayüzündeki **Vision Model** kutusuna bu kimliği yapıştırın. **Bağlantı Testi** yeşilse modele istek gidebilir.

GPU şart değildir; hız ve görüntü kalitesi tamamen LM Studio'daki modele bağlıdır. Bu depo modeli çalıştırmaz.

## Kurulum ve çalıştırma

```bash
pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Tarayıcıdan `http://127.0.0.1:8000` adresine giderek kullanabilirsiniz. Sunucu yalnızca loopback arayüzüne bağlanır.

## Yapılandırma

Arayüzden değişenler: LM Studio API adresi, model kimliği, DPI (sunucu 72–400 aralığına sıkıştırır) ve sayfa aralığı.

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

GPU veya LM Studio gerekmez. LLM çağrıları sahte istemciyle yürür.

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Lisans

AtlasDoc, [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0) ile lisanslanmıştır.

Copyright (C) 2026 Selçuk Metcan

Bu seçim, bağımlılık olan **PyMuPDF**'in AGPL-3.0 veya Artifex ticari lisansı şeklinde çift lisanslı olmasından gelir. Uygulama bir ağ hizmeti olarak çalıştığı için AGPL'in ağ kullanımı koşulları geçerlidir. Başka bir proje lisansı tercih edilirse `LICENSE` ve bu bölüm güncellenmelidir; PyMuPDF'in kendi lisansı ayrıca geçerlidir.
