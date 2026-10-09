"""The packaged API must retain HTTPS only for its explicitly trusted proxy."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from fastapi import FastAPI
import httpx
import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker Compose is not installed")
async def test_compose_trusts_only_the_configured_gateway_for_redirects():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["docker", "compose", "-f", str(root / "docker-compose.yml"), "config", "--format", "json"],
        env={**os.environ, "RANGE42_TRUSTED_PROXY_IPS": "10.81.0.10"},
        capture_output=True, text=True, check=True,
    )
    environment = json.loads(result.stdout)["services"]["api"]["environment"]
    app = FastAPI()

    @app.get("/deployments/")
    def deployments():
        return []

    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=environment.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))
    for address, scheme in (("10.81.0.10", "https"), ("10.82.0.10", "http")):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=wrapped, client=(address, 12345)),
            base_url="http://api.alpha.example.test",
        ) as client:
            response = await client.get("/deployments", headers={"X-Forwarded-Proto": "https"})
        assert response.status_code == 307
        assert response.headers["location"] == f"{scheme}://api.alpha.example.test/deployments/"
