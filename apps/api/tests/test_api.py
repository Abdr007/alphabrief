"""HTTP surface: contracts, auth, validation and the hardening middleware."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.core.middleware import SECURITY_HEADERS
from app.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


class TestHealthAndDiscovery:
    async def test_health_reports_configuration(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["engine"] in ("anthropic", "deterministic")
        assert body["models"]["supervisor"]
        assert body["watchlist"]

    async def test_mcp_tools_are_documented_over_http(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/mcp/tools")
        assert response.status_code == 200
        tools = response.json()
        assert {tool["name"] for tool in tools} == {
            "get_price_history",
            "get_fundamentals",
            "compute_metrics",
            "fetch_rss_news",
        }

    async def test_event_kinds_are_published(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/events/kinds")
        assert response.status_code == 200
        assert "mcp.tool_call" in response.json()


class TestSecurityHeaders:
    async def test_every_response_is_hardened(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health")
        for header, value in SECURITY_HEADERS.items():
            assert response.headers.get(header) == value

    async def test_csp_forbids_script_execution(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health")
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]


class TestInputValidation:
    async def test_unknown_fields_are_rejected(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/runs", json={"tickers": ["AAPL"], "sneaky": 1})
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "tickers",
        [
            [],
            ["A" * 40],
            ["AAPL; DROP TABLE runs"],
            ["../../etc/passwd"],
            ["<script>alert(1)</script>"],
            [f"TCK{index}" for index in range(50)],
            ["AAPL"] * 500,
        ],
    )
    async def test_hostile_watchlists_are_refused(
        self, client: httpx.AsyncClient, tickers: list[str]
    ) -> None:
        response = await client.post("/v1/runs", json={"tickers": tickers})
        assert response.status_code == 422

    async def test_duplicate_tickers_collapse_rather_than_fail(
        self, client: httpx.AsyncClient
    ) -> None:
        """A user pasting the same symbol twice is a typo, not an attack."""
        from app.api.schemas import CreateRunRequest

        request = CreateRunRequest(tickers=["AAPL", "aapl", " AAPL "])
        assert request.tickers == ["AAPL"]

    async def test_validation_errors_do_not_echo_the_body(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/runs", json={"tickers": ["<script>"]})
        assert response.status_code == 422
        assert "<script>" not in response.text

    async def test_unknown_mode_is_refused(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/runs", json={"tickers": ["AAPL"], "mode": "root"})
        assert response.status_code == 422

    async def test_oversized_body_is_rejected(self, client: httpx.AsyncClient) -> None:
        payload = {"tickers": ["AAPL"], "note": "x" * 200_000}
        response = await client.post("/v1/runs", json=payload)
        assert response.status_code in (413, 422)


class TestApprovalAuth:
    async def test_decision_requires_a_token(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/runs/run_missing/decision", json={"action": "approve"})
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    async def test_wrong_token_is_rejected(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/v1/runs/run_missing/decision",
            json={"action": "approve"},
            headers={"Authorization": "Bearer not-the-token"},
        )
        assert response.status_code == 401

    async def test_correct_token_reaches_the_handler(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/v1/runs/run_missing/decision",
            json={"action": "approve"},
            headers={"Authorization": "Bearer test-approval-token"},
        )
        # Authenticated, so we get past auth and fail on the unknown run instead.
        assert response.status_code == 404

    async def test_unknown_action_is_refused(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/v1/runs/run_missing/decision",
            json={"action": "delete_everything"},
            headers={"Authorization": "Bearer test-approval-token"},
        )
        assert response.status_code == 422


class TestNotFoundPaths:
    async def test_unknown_run_returns_404(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/v1/runs/run_nope")).status_code == 404

    async def test_unknown_gate_returns_404(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/v1/runs/run_nope/gate")).status_code == 404

    async def test_archive_is_listable(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/runs?limit=5")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    async def test_archive_limit_is_bounded(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/v1/runs?limit=99999")).status_code == 422


class TestRateLimiting:
    async def test_repeated_writes_are_throttled(self, client: httpx.AsyncClient) -> None:
        statuses = []
        for _ in range(40):
            response = await client.post(
                "/v1/runs/run_missing/decision",
                json={"action": "approve"},
                headers={"Authorization": "Bearer wrong"},
            )
            statuses.append(response.status_code)
        assert 429 in statuses
        limited = next(s for s in statuses if s == 429)
        assert limited == 429

    async def test_reads_are_not_rate_limited(self, client: httpx.AsyncClient) -> None:
        for _ in range(40):
            assert (await client.get("/health")).status_code == 200
