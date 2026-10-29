# One image for the worker, the relay and the migration job; the k8s command picks the role.
FROM python:3.14-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_HTTP_TIMEOUT=300 UV_PYTHON_DOWNLOADS=never HF_HOME=/opt/hf
WORKDIR /app

# Dependencies first: this layer (torch, Docling) only rebuilds when the lock file changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

# Bake Docling's models into the image by parsing a blank PDF with the app's own parser config,
# so pods start without network access to Hugging Face.
RUN .venv/bin/python -c "\
import io, pypdfium2 as pdfium; \
from enterprise_rag.ingestion.parsing import make_docling_parse; \
pdf = pdfium.PdfDocument.new(); pdf.new_page(200, 200); buf = io.BytesIO(); pdf.save(buf); \
make_docling_parse()(buf.getvalue(), 'warmup.pdf')"

FROM python:3.14-slim-trixie
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /opt/hf /opt/hf
COPY --from=build /app /app
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH HF_HOME=/opt/hf HF_HUB_OFFLINE=1 PYTHONUNBUFFERED=1
USER 10001
