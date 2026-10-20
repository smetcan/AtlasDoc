"""`atlasdoc serve` and `atlasdoc convert`."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from app import max_upload_bytes
from converter import (
    default_model_name,
    default_provider,
    process_pdf_stream,
    resolve_provider_config,
)


def combine_markdown(pages, separators: bool) -> str:
    """Sayfa markdown'larını arayüzdeki birleştirme kuralıyla bir araya getirir."""
    chunks = []
    for page, markdown in pages:
        text = (markdown or "").strip("\n")
        if not text.strip():
            continue
        chunks.append(f"<!-- Sayfa {page} -->\n{text}" if separators else text)
    if not chunks:
        return ""
    joiner = "\n\n---\n\n" if separators else "\n\n"
    return joiner.join(chunks) + "\n"


def _convert(args) -> int:
    source = Path(args.input)
    if not source.is_file():
        print(f"error: file not found: {source}", file=sys.stderr)
        return 1
    if source.suffix.lower() != ".pdf":
        print("error: input must be a .pdf file", file=sys.stderr)
        return 1

    output = Path(args.output)
    if output.exists() and output.is_dir():
        print(f"error: output is a directory: {output}", file=sys.stderr)
        return 1

    pdf_bytes = source.read_bytes()
    limit = max_upload_bytes()
    if len(pdf_bytes) > limit:
        print(
            f"error: PDF is larger than the {limit // (1024 * 1024)} MB upload limit",
            file=sys.stderr,
        )
        return 1

    provider = (args.provider or "").strip() or default_provider()
    api_key = args.api_key if args.api_key is not None else os.environ.get("ATLASDOC_API_KEY", "")
    _, base_url, _, _ = resolve_provider_config(
        provider=provider,
        base_url=(args.base_url or "").strip(),
        api_key=api_key,
    )
    model_name = (args.model or "").strip() or default_model_name()
    dpi = max(72, min(400, int(args.dpi)))

    async def _run():
        pages = []
        failed = []
        truncated = []
        fatal = None
        async for event in process_pdf_stream(
            pdf_bytes=pdf_bytes,
            base_url=base_url,
            model_name=model_name,
            dpi=dpi,
            page_ranges=args.pages or "",
            provider=provider,
            api_key=api_key,
        ):
            kind = event.get("type")
            if kind == "init":
                print(
                    f"converting {event.get('total_pages')} page(s) with {model_name}",
                    file=sys.stderr,
                )
            elif kind == "page_done":
                pages.append((event.get("page"), event.get("markdown") or ""))
                note = " truncated" if event.get("truncated") else ""
                print(f"page {event.get('page')} done{note}", file=sys.stderr)
            elif kind == "page_error":
                if event.get("page") is not None:
                    failed.append(event["page"])
                print(event.get("message") or "page failed", file=sys.stderr)
            elif kind == "error":
                fatal = event.get("message") or "conversion failed"
                print(fatal, file=sys.stderr)
            elif kind == "complete":
                failed = list(event.get("failed_pages") or failed)
                truncated = list(event.get("truncated_pages") or [])
        return pages, failed, truncated, fatal

    pages, failed, truncated, fatal = asyncio.run(_run())
    text = combine_markdown(pages, separators=args.page_separators)
    if text:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"wrote {output}", file=sys.stderr)
    if fatal and not text:
        return 1
    if failed or fatal:
        if failed:
            print(f"failed pages: {', '.join(str(page) for page in failed)}", file=sys.stderr)
        return 1
    if truncated:
        print(
            f"truncated pages: {', '.join(str(page) for page in truncated)}",
            file=sys.stderr,
        )
    if not text:
        print("error: model returned no markdown", file=sys.stderr)
        return 1
    return 0


def _serve(args) -> int:
    import uvicorn

    uvicorn.run("app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlasdoc",
        description="Convert PDFs to Markdown with a local vision model.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the web UI")
    serve.add_argument(
        "--host",
        default=os.environ.get("ATLASDOC_HOST", "127.0.0.1"),
        help="bind address (default 127.0.0.1, or ATLASDOC_HOST)",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ATLASDOC_PORT", "8000")),
        help="port (default 8000, or ATLASDOC_PORT)",
    )
    serve.add_argument("--reload", action="store_true", help="restart when code changes")
    serve.set_defaults(func=_serve)

    convert = sub.add_parser("convert", help="convert one PDF on the command line")
    convert.add_argument("input", help="PDF file")
    convert.add_argument("-o", "--output", required=True, help="markdown file to write")
    convert.add_argument("--pages", default="", help="page ranges, for example 1-5, 8, 20-")
    convert.add_argument("--dpi", type=int, default=200, help="render DPI from 72 to 400")
    convert.add_argument("--provider", default=None, help="provider id (default lmstudio, or ATLASDOC_PROVIDER)")
    convert.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible base URL (or ATLASDOC_BASE_URL / LMSTUDIO_BASE_URL)",
    )
    convert.add_argument("--model", default=None, help="model id (or ATLASDOC_MODEL)")
    convert.add_argument("--api-key", default=None, help="API key (or ATLASDOC_API_KEY)")
    convert.add_argument(
        "--page-separators",
        action="store_true",
        help="insert a marker and --- between pages",
    )
    convert.set_defaults(func=_convert)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
