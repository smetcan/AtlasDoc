# AtlasDoc

[English](README.md)

Yerel bir programdır. PDF'in her sayfasını görüntü olarak çizer, üstü çizili metni maskeler ve bir vision modele Markdown yazdırır. Barındırılan bir servis yoktur.

Metin katmanını okuyan ayrıştırıcılar, katman düzgünse hızlı ve kesindir; sayfada görüneni (üstü çizili cümle, kaşe, form, tarama) kaçırır. AtlasDoc sayfa görüntüsünü modele gönderir. Model metni atlayabilir veya uydurabilir. Modeli kendi makinenizde çalıştırırsanız sayfa görüntüleri makineden çıkmaz.

![AtlasDoc demo](docs/demo.gif)

*Demo GIF henüz kaydedilmedi. Kaydı `docs/demo.gif` olarak koyun; bu görsel onu gösterir.*

## Hızlı başlangıç

Python 3.11 veya daha yeni.

```bash
pip install .
atlasdoc serve
```

Tarayıcıdan `http://127.0.0.1:8000` adresini açın. Arayüz Tailwind, marked, DOMPurify, Mermaid ve yazı tiplerini ağdan yüklemez. Dosyalar depodadır ve uygulama sunar. Ayrıntı: [atlasdoc/static/vendor/NOTICE.md](atlasdoc/static/vendor/NOTICE.md).

Model ayrıca kurulur. Alışılmış yol, aynı bilgisayardaki LM Studio'dur.

### Docker

```bash
docker compose up --build
```

Ardından `http://127.0.0.1:8000`.

Kapsayıcının içindeki `localhost`, kapsayıcının kendisidir. Compose şunu ayarlar:

```text
ATLASDOC_BASE_URL=http://host.docker.internal:1234/v1
```

Linux'ta `host.docker.internal` için `extra_hosts: host-gateway` vardır. Arayüz, kayıtlı profil yokken bu adresi LM Studio varsayılanı yapar.

LM Studio yalnızca `127.0.0.1` üzerinde dinliyorsa, macOS ve Windows'taki Docker Desktop çoğu zaman `host.docker.internal` ile ona ulaşır. Linux'ta `host-gateway` köprü adresidir, loopback değildir; bağlantı reddedilebilir. O zaman LM Studio'yu tüm arayüzlere bağlayın ya da host ağını kullanın:

```bash
docker run --rm --network host -e ATLASDOC_BASE_URL=http://127.0.0.1:1234/v1 atlasdoc
```

`ATLASDOC_BASE_URL`, LM Studio taban adresidir. `LMSTUDIO_BASE_URL` aynı ayarın diğer adıdır. Arayüzde yazdığınız adres ve `atlasdoc convert --base-url` ortam değişkeninin önüne geçer.

`pip install -r requirements.txt` ve `python -m uvicorn app:app --host 127.0.0.1 --port 8000` bir çalışma kopyasından hâlâ çalışır.

## LM Studio

1. [LM Studio](https://lmstudio.ai/) kurun.
2. Görüntü kabul eden bir **vision** model indirin. LM Studio'da görünen kimlik, kullanmanız gereken kimliktir. Hugging Face adından farklı olabilir.
3. Yerel sunucuyu başlatın. OpenAI uyumlu adres çoğunlukla `http://localhost:1234/v1` olur.
4. AtlasDoc'ta **Modeller** penceresinden LM Studio profilini seçin, sunucudaki modelleri listeleyin ve kaydedin.

Sağlık kontrolü yalnızca yüklü modelleri sorar. Dönüşüm, sayfa görüntülerini o sunucuya gönderir.

Arayüz Ollama ve başka OpenAI uyumlu uçlar (bulut API'leri dahil) için profil de saklayabilir. Bulut profili sayfa görüntülerini o sağlayıcıya gönderir. Yerel profiller göndermez. Profiller ve API anahtarları sunucuya yazılmaz; tarayıcının `localStorage` alanındadır.

## İşe yarama ihtimali olan modeller

Bunlar öneridir, kıyaslama değildir. Bu depo modelleri birbirine karşı ölçmedi; burada doğruluk sayısı yoktur.

- **`google/gemma-4-e4b`**, uygulamanın doldurduğu varsayılan. Hugging Face modeli `google/gemma-4-E4B-it`. LM Studio derlemeleri çoğu zaman `lmstudio-community` GGUF'unu kullanır. Küçük bir vision model olduğu için varsayılandır; bu depoda bir karşılaştırmayı kazandığı için değil.
- **Yaklaşık 7B–8B Qwen2.5-VL veya Qwen3-VL**, belgeye yönelik bir model istiyor ve belleğiniz yetiyorsa. LM Studio'daki kimlik kuruluma göre değişir. Yüksek DPI daha çok görüntü token'ı demektir. Sayfa düşer veya kesilirse DPI'ı azaltın.
- **Ollama etiketleri** (`gemma3`, `qwen2.5vl` gibi), Ollama profilini kullanıyorsanız (`http://localhost:11434/v1`).

Kimliği LM Studio'nun sunucu listesinden okuyun. Yüklü kimliğe benzemekle birlikte aynı olmayan bir ad isteği düşürür.

## Komut satırı

`atlasdoc convert`, web arayüzüyle aynı sayfa hattını kullanır.

```bash
atlasdoc convert input.pdf -o out.md
atlasdoc convert input.pdf -o out.md --pages 1-5,8,20- --dpi 150
atlasdoc convert input.pdf -o out.md --page-separators
```

`--pages` arayüzle aynı sözdizimindedir (`1-5, 8, 20-`). Çıktı UTF-8 Markdown'dır. İlerleme stderr'e yazılır. Bir sayfa başarısız olursa dosyada başarılı sayfalar durur ve komut 1 ile çıkar.

```bash
atlasdoc serve
atlasdoc serve --host 127.0.0.1 --port 8000
```

## Yapılandırma

| Değişken | Varsayılan | Anlamı |
| --- | --- | --- |
| `ATLASDOC_BASE_URL` | `http://localhost:1234/v1` | LM Studio (veya OpenAI uyumlu) taban adresi. `LMSTUDIO_BASE_URL` yedek addır. |
| `ATLASDOC_MODEL` | `google/gemma-4-e4b` | Kayıtlı profil yokken ve CLI'da kullanılan model kimliği. |
| `ATLASDOC_PROVIDER` | `lmstudio` | Sağlayıcı kimliği. |
| `ATLASDOC_API_KEY` | boş | CLI için API anahtarı, sağlayıcı istiyorsa. |
| `ATLASDOC_HOST` | `127.0.0.1` | `atlasdoc serve` bağlandığı adres. Docker `0.0.0.0` kullanır. |
| `ATLASDOC_PORT` | `8000` | `atlasdoc serve` portu. |
| `ATLASDOC_MAX_UPLOAD_MB` | `100` | En büyük PDF, megabayt. 1'den küçük değerler 1 sayılır. Arayüz ve CLI için geçerlidir. |
| `ATLASDOC_TIMEOUT` | `120` | Bir sayfa isteğinin saniye sınırı. Süre dolunca o sayfa düşer, iş sürer. |
| `ATLASDOC_MAX_RETRIES` | `1` | OpenAI istemcisinin ek denemesi. `0` tek denemedir. |
| `ATLASDOC_MAX_TOKENS` | `8192` | Sayfa başına `max_tokens`. Uzunluk yüzünden biten yanıt kesik işaretlenir ve yine de yazılır. |

Arayüzdeki DPI 72–400 aralığına sıkıştırılır. CLI de aynısını yapar.

## Sınırlar

- Üstü çizili metin temizliği vektör çizgilerine ve StrikeOut anotasyonlarına bakar, görüntü gönderilmeden o gliflerin üstünü boyar. Yalnızca taranmış bir resimde duran çizgiyi görmez. Alt çizgiye ve tablo çizgisine dokunmaz.
- Vision model, maskenin kapsamadığı metni de atlayabilir veya uydurabilir.
- Sayfalar sırayla işlenir. Uzun PDF, modelin sürdüğü kadar sürer.
- Web arayüzü çevrimdışıdır. Model paketlenmez. LM Studio, Ollama veya başka bir sunucuyu siz çalıştırırsınız.

## Geliştirme

```bash
pip install -e ".[dev]"
pytest
```

CSS tarayıcıda üretilmez; derlenmiş hali depoya konur. `atlasdoc/static/index.html` içindeki sınıflar değişirse:

```bash
cd frontend && npm install && npm run build:css
```

## Lisans

AtlasDoc, [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0) ile lisanslanmıştır.

Copyright (C) 2026 Selçuk Metcan

AGPL-3.0, **PyMuPDF** AGPL-3.0 veya Artifex ticari lisanslı olduğu için seçildi. AtlasDoc bir ağ programıdır; AGPL'in ağ koşulları geçerlidir. Proje lisansı değişirse PyMuPDF'in kendi lisansı ayrıca geçerlidir. Arayüze konan dosyaların lisansları için [atlasdoc/static/vendor/NOTICE.md](atlasdoc/static/vendor/NOTICE.md) dosyasına bakın.
