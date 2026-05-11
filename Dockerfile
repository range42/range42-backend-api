# ─── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.13-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY requirements.yml .
RUN /opt/venv/bin/ansible-galaxy collection install \
    -r requirements.yml \
    -p /usr/share/ansible/collections

# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS runtime

# Match host UID/GID at build time so SSH key volume permissions align.
# Override with: docker build --build-arg UID=$(id -u) --build-arg GID=$(id -g)
ARG UID=1000
ARG GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g "${GID}" range42 \
    && useradd -u "${UID}" -g "${GID}" -m -d /home/range42 --no-log-init range42

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /usr/share/ansible/collections /usr/share/ansible/collections

COPY app/ app/
COPY playbooks/ playbooks/
COPY inventory/ inventory/

RUN chown -R range42:range42 /app

ENV PATH="/opt/venv/bin:$PATH"
ENV HOME=/home/range42
ENV PYTHONPATH=/app
ENV PROJECT_ROOT_DIR=/app
ENV API_BACKEND_WWWAPP_PLAYBOOKS_DIR=/app/
ENV API_BACKEND_INVENTORY_DIR=/app/inventory/
ENV ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections

USER range42

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/docs/openapi.json').raise_for_status()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
