FROM python:3.12-slim

# Install system deps for ansible and ssh
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Ansible collections
COPY requirements.yml .
RUN ansible-galaxy collection install -r requirements.yml -p /usr/share/ansible/collections

# Copy application
COPY app/ app/
COPY playbooks/ playbooks/
COPY inventory/ inventory/

# Set env defaults
ENV PROJECT_ROOT_DIR=/app
ENV API_BACKEND_WWWAPP_PLAYBOOKS_DIR=/app/
ENV API_BACKEND_INVENTORY_DIR=/app/inventory/
ENV HOST=0.0.0.0
ENV PORT=8000
ENV PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/docs/openapi.json').raise_for_status()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
