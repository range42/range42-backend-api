"""Optional bridges execute within the same foundation native adapter call."""

import pytest

from app.core import foundation_runner


@pytest.mark.asyncio
async def test_original_foundation_attempt_runs_optional_network_stage_in_order(
    monkeypatch,
):
    calls = []

    async def templates(config, request):
        calls.append(("templates", request))
        return {"status": "complete", "request_id": "a" * 32, "foundation_ready": True}

    async def networks(config, request):
        calls.append(("networks", request))
        return {"status": "ready", "network_ready": True}

    monkeypatch.setattr(foundation_runner, "run_original", templates)
    monkeypatch.setattr("app.core.environment_network_execution.run_original", networks)
    assert hasattr(foundation_runner, "run_attempt"), (
        "Missing network stage in the existing native foundation runner"
    )
    foundation = {"original": "foundation"}
    network = {"original": "network"}
    result = await foundation_runner.run_attempt(
        object(), foundation, network_configuration=object(), network_request=network
    )
    assert calls == [("templates", foundation), ("networks", network)]
    assert result["foundation_ready"] and result["network_ready"]


@pytest.mark.asyncio
async def test_existing_template_only_attempt_does_not_require_any_network_capability(
    monkeypatch,
):
    async def templates(config, request):
        return {"status": "complete", "foundation_ready": True}

    monkeypatch.setattr(foundation_runner, "run_original", templates)
    assert hasattr(foundation_runner, "run_attempt"), (
        "Missing compatible foundation attempt adapter"
    )
    assert await foundation_runner.run_attempt(object(), {}) == {
        "status": "complete",
        "foundation_ready": True,
    }
