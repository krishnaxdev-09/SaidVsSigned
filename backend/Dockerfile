FROM python:3.12.8-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv==0.11.26
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/opt/venv/bin:$PATH"
COPY . .
# Docling/RapidOCR download their models on first use (T2.3 Docker smoke test: a system user without a writable
# home or model dir could not store them).
RUN useradd --system --create-home --uid 10001 app && chown -R app /srv \
 && mkdir -p /home/app/.cache && chown -R app /home/app/.cache \
 && mkdir -p /opt/venv/lib/python3.12/site-packages/rapidocr/models \
 && chown app /opt/venv/lib/python3.12/site-packages/rapidocr/models  # RapidOCR caches models in its package dir
ENV HOME=/home/app HF_HOME=/home/app/.cache/huggingface
USER app
EXPOSE 8000
