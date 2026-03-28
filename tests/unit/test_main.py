from __future__ import annotations

from unittest.mock import patch

import pytest

from ingress_operator.main import main


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPNSENSE_HOST", "opnsense.test")
    monkeypatch.setenv("OPNSENSE_API_KEY", "key")
    monkeypatch.setenv("OPNSENSE_API_SECRET", "secret")


class TestMain:
    def test_liveness_endpoint_is_url_string(self, base_env: None) -> None:
        with patch("kopf.run") as mock_run:
            main()
            mock_run.assert_called_once()
            endpoint = mock_run.call_args.kwargs["liveness_endpoint"]
            assert isinstance(endpoint, str), "liveness_endpoint must be a string, not a tuple"
            assert endpoint.startswith("http://")

    def test_liveness_endpoint_default(self, base_env: None) -> None:
        with patch("kopf.run") as mock_run:
            main()
            endpoint = mock_run.call_args.kwargs["liveness_endpoint"]
            assert endpoint == "http://0.0.0.0:8080"

    def test_liveness_endpoint_custom(self, base_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPNSENSE_HEALTH_HOST", "127.0.0.1")
        monkeypatch.setenv("OPNSENSE_HEALTH_PORT", "9090")
        with patch("kopf.run") as mock_run:
            main()
            endpoint = mock_run.call_args.kwargs["liveness_endpoint"]
            assert endpoint == "http://127.0.0.1:9090"
