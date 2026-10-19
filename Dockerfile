FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ATLASDOC_HOST=0.0.0.0 \
    ATLASDOC_PORT=8000 \
    ATLASDOC_BASE_URL=http://host.docker.internal:1234/v1

WORKDIR /app

COPY pyproject.toml README.md LICENSE requirements.txt MANIFEST.in ./
COPY app.py converter.py ./
COPY atlasdoc ./atlasdoc

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["atlasdoc", "serve"]
