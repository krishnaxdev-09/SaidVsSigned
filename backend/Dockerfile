FROM python:3.12.8-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv
RUN pip install --no-cache-dir uv==0.11.26
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/opt/venv/bin:$PATH"
COPY . .
RUN useradd --system --uid 10001 app && chown -R app /srv
USER app
EXPOSE 8000
