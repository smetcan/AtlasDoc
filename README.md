# AtlasDoc

[Türkçe](README.tr.md)

AtlasDoc turns a PDF into Markdown by **looking at each page**. It renders the page, masks strikethrough text, and asks a vision model for Markdown. It is a local program you run yourself. There is no hosted service.

Deterministic parsers read the PDF text layer. That is fast and exact when the text layer is good, and it misses what the page actually looks like: a strike through a sentence, a stamp, a form, a scan. AtlasDoc sends the page image to a vision model instead, so the model can follow the page. The cost is that the model can skip or invent text. Keep the model on your own machine and the page images stay there.

![AtlasDoc demo](docs/demo.gif)

*Demo GIF is not recorded yet. Save a recording as `docs/demo.gif` and this image will show it.*

## Quick start

Python 3.11 or newer.

```bash
pip install .
atlasdoc serve
```

Open http://127.0.0.1:8000. The page does not load Tailwind, marked, DOMPurify, Mermaid, or fonts from the network. Those files are in the repo and served by the app. See [atlasdoc/static/vendor/NOTICE.md](atlasdoc/static/vendor/NOTICE.md).

You still need a vision model. The usual one is LM Studio on the same computer.

### Docker

```bash
docker compose up --build
```

Then open http://127.0.0.1:8000.

The container cannot see LM Studio at `localhost`. `localhost` inside the container is the container. Compose sets:

```text
ATLASDOC_BASE_URL=http://host.docker.internal:1234/v1
```

and adds `host.docker.internal` (`extra_hosts: host-gateway`, which Linux needs). The web UI picks that up as the default LM Studio address.

LM Studio must accept that connection. If it listens only on `127.0.0.1`, Docker Desktop on macOS and Windows often still reaches it through `host.docker.internal`. On Linux it often does not, because `host-gateway` is the host's bridge address, not its loopback. If the health check cannot connect, either bind LM Studio's server to all interfaces, or run AtlasDoc with host networking and point it back at loopback:

```bash
docker run --rm --network host -e ATLASDOC_BASE_URL=http://127.0.0.1:1234/v1 atlasdoc
```

`ATLASDOC_BASE_URL` is the LM Studio base URL. `LMSTUDIO_BASE_URL` is accepted as another name for the same setting. A value you type in the UI, or pass to `atlasdoc convert --base-url`, wins over the environment.

`pip install -r requirements.txt` and `python -m uvicorn app:app --host 127.0.0.1 --port 8000` still start the app from a checkout.

## LM Studio

1. Install [LM Studio](https://lmstudio.ai/).
2. Download a **vision** model (one that accepts images). The id shown in LM Studio is the id you must use. It often differs from the Hugging Face repo name.
3. Start the local server. The OpenAI-compatible base URL is usually `http://localhost:1234/v1`.
4. In AtlasDoc, open **Modeller**, choose the LM Studio profile, list models from the server, and save.

The health check only asks LM Studio which models are loaded. Conversion sends page images to that server.

The UI can also store profiles for Ollama and for other OpenAI-compatible endpoints, including cloud APIs. A cloud profile sends page images to that provider. The local profiles do not.

## Models that tend to work

These are suggestions, not a benchmark. This project has not scored them against each other, and there are no accuracy numbers here.

- **`google/gemma-4-e4b`**, the default filled in by the app. The Hugging Face model is `google/gemma-4-E4B-it`. LM Studio builds often use a GGUF from `lmstudio-community`. It is a small vision model, which is why it is the default, not because it won a comparison in this repo.
- **Qwen2.5-VL or Qwen3-VL, around 7B–8B**, if you want a document-oriented vision model and have the memory for it. Their ids in LM Studio vary. Higher DPI makes more image tokens. If a page fails or gets cut off, lower the DPI.
- **Ollama tags** such as `gemma3` or `qwen2.5vl`, if you use the Ollama profile (`http://localhost:11434/v1`).

Read the model id from LM Studio's server list. A name that is close, but not the loaded id, will fail the request.

## Command line

`atlasdoc convert` uses the same page pipeline as the web UI.

```bash
atlasdoc convert input.pdf -o out.md
atlasdoc convert input.pdf -o out.md --pages 1-5,8,20- --dpi 150
atlasdoc convert input.pdf -o out.md --page-separators
```

`--pages` uses the same syntax as the UI (`1-5, 8, 20-`). Output is UTF-8 Markdown. Progress is printed on stderr. If any page fails, the file still contains the pages that succeeded and the command exits with status 1.

```bash
atlasdoc serve
atlasdoc serve --host 127.0.0.1 --port 8000
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `ATLASDOC_BASE_URL` | `http://localhost:1234/v1` | LM Studio (or other OpenAI-compatible) base URL. `LMSTUDIO_BASE_URL` is a fallback name. |
| `ATLASDOC_MODEL` | `google/gemma-4-e4b` | Model id used when the UI has no saved profile, and by the CLI. |
| `ATLASDOC_PROVIDER` | `lmstudio` | Provider id (`lmstudio`, `ollama`, or another id the UI knows). |
| `ATLASDOC_API_KEY` | empty | API key for the CLI, when the provider needs one. |
| `ATLASDOC_HOST` | `127.0.0.1` | Address `atlasdoc serve` binds. Docker sets `0.0.0.0`. |
| `ATLASDOC_PORT` | `8000` | Port for `atlasdoc serve`. |
| `ATLASDOC_MAX_UPLOAD_MB` | `100` | Largest PDF, in megabytes. Values below 1 are treated as 1. Applies to the UI and the CLI. |
| `ATLASDOC_TIMEOUT` | `120` | Seconds for one page request. A timeout fails that page and the job continues. |
| `ATLASDOC_MAX_RETRIES` | `1` | Extra attempts by the OpenAI client. `0` means one attempt. |
| `ATLASDOC_MAX_TOKENS` | `8192` | `max_tokens` per page. A response that stops for length is marked truncated and still kept. |

DPI in the UI is clamped to 72–400. The CLI does the same.

Model profiles and API keys in the web UI are stored in the browser's `localStorage`, not on the server.

## Limitations

- Strikethrough removal looks at vector lines and StrikeOut annotations, then paints over those glyphs before the image is sent. It does not detect a strike that exists only in a scanned picture. Underlines and table rules are left alone.
- The vision model can still drop or invent text, including text the mask did not cover.
- Pages are processed one at a time. A long PDF takes as long as the model takes.
- The web UI is offline. The model is not bundled. You run LM Studio, Ollama, or another server yourself.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The committed CSS is built, not generated in the browser. After changing classes in `atlasdoc/static/index.html`:

```bash
cd frontend && npm install && npm run build:css
```

## License

AtlasDoc is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

Copyright (C) 2026 Selçuk Metcan

AGPL-3.0 is used because **PyMuPDF** is AGPL-3.0 or an Artifex commercial license. AtlasDoc is a network program, so the AGPL's network terms apply. PyMuPDF's license still applies on its own if you choose a different license for this project. Vendored UI files keep their own licenses; see [atlasdoc/static/vendor/NOTICE.md](atlasdoc/static/vendor/NOTICE.md).
