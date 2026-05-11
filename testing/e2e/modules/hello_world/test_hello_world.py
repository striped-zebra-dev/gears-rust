"""E2E tests for the hello-world module.

Usage:
    E2E_BINARY=skip python3 -m pytest testing/e2e/modules/hello_world/ -v

Requires the cyberware-example-server running with --features hello-world.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8087")
ENDPOINT = f"{BASE_URL}/hello-world/v1/hello"
TIMEOUT = 10


@pytest.fixture(scope="module")
def _check_server():
    """Skip if the server is not reachable."""
    try:
        resp = httpx.get(ENDPOINT, timeout=5)
        if resp.status_code == 404:
            pytest.skip("hello-world endpoint not found (feature not enabled?)")
    except httpx.ConnectError:
        pytest.skip(f"Server not reachable at {BASE_URL}")


class TestHelloWorldEndpoint:
    """Tests for GET /hello-world/v1/hello."""

    def test_returns_200(self, _check_server):
        resp = httpx.get(ENDPOINT, timeout=TIMEOUT)
        assert resp.status_code == 200

    def test_response_has_message(self, _check_server):
        resp = httpx.get(ENDPOINT, timeout=TIMEOUT)
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Hello World"

    def test_response_has_delay_seconds(self, _check_server):
        resp = httpx.get(ENDPOINT, timeout=TIMEOUT)
        assert resp.status_code == 200
        data = resp.json()
        assert "delay_seconds" in data
        delay = data["delay_seconds"]
        # Monday=0.1, Sunday=0.7
        assert 0.1 <= delay <= 0.7, f"delay_seconds out of range: {delay}"

    def test_response_delay_matches_day_of_week(self, _check_server):
        """Verify that delay_seconds equals 0.1 * current ISO day-of-week."""
        import datetime

        resp = httpx.get(ENDPOINT, timeout=TIMEOUT)
        assert resp.status_code == 200
        data = resp.json()
        delay = data["delay_seconds"]

        # isoweekday(): Monday=1, Sunday=7
        expected = 0.1 * datetime.datetime.now().isoweekday()
        assert abs(delay - expected) < 1e-9, (
            f"Expected delay {expected}, got {delay}"
        )

    def test_actual_response_time_includes_delay(self, _check_server):
        """Verify the server actually sleeps for the declared delay."""
        start = time.monotonic()
        resp = httpx.get(ENDPOINT, timeout=TIMEOUT)
        elapsed = time.monotonic() - start

        assert resp.status_code == 200
        delay = resp.json()["delay_seconds"]

        # Elapsed should be at least the declared delay (minus small tolerance)
        assert elapsed >= delay - 0.05, (
            f"Response took {elapsed:.3f}s but delay_seconds={delay}"
        )

    def test_content_type_is_json(self, _check_server):
        resp = httpx.get(ENDPOINT, timeout=TIMEOUT)
        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("content-type", "")

    def test_response_schema(self, _check_server):
        """Verify response contains exactly the expected fields."""
        resp = httpx.get(ENDPOINT, timeout=TIMEOUT)
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"message", "delay_seconds"}
        assert isinstance(data["message"], str)
        assert isinstance(data["delay_seconds"], float)
