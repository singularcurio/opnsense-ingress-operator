from __future__ import annotations

import pytest

from ingress_operator.config import Config
from ingress_operator.handlers.ingress import _build_desired, _get_ingress_ip


@pytest.fixture
def config() -> Config:
    return Config(
        host="opnsense.test",
        api_key="key",
        api_secret="secret",
        annotation_prefix="opnsense.io",
    )


class TestGetIngressIP:
    def test_returns_lb_ip(self, config: Config) -> None:
        status = {"loadBalancer": {"ingress": [{"ip": "10.0.0.5"}]}}
        assert _get_ingress_ip(status, {}, config) == "10.0.0.5"

    def test_annotation_overrides_lb_ip(self, config: Config) -> None:
        status = {"loadBalancer": {"ingress": [{"ip": "10.0.0.5"}]}}
        annotations = {"opnsense.io/target-ip": "192.168.1.100"}
        assert _get_ingress_ip(status, annotations, config) == "192.168.1.100"

    def test_returns_none_when_no_ip(self, config: Config) -> None:
        assert _get_ingress_ip({}, {}, config) is None

    def test_returns_none_when_lb_has_no_ip(self, config: Config) -> None:
        status = {"loadBalancer": {"ingress": [{"hostname": "lb.example.com"}]}}
        assert _get_ingress_ip(status, {}, config) is None

    def test_skips_lb_entry_without_ip(self, config: Config) -> None:
        status = {"loadBalancer": {"ingress": [{"hostname": "lb.example.com"}, {"ip": "10.0.0.9"}]}}
        assert _get_ingress_ip(status, {}, config) == "10.0.0.9"


class TestBuildDesired:
    def test_basic_rule(self, config: Config) -> None:
        spec = {"rules": [{"host": "app.example.com", "http": {}}]}
        entries = _build_desired(spec, "10.0.0.1", {}, "my-ingress", "default", config)
        assert len(entries) == 1
        e = entries[0]
        assert e.fqdn == "app.example.com"
        assert e.ip == "10.0.0.1"
        assert e.hostname == "app"
        assert e.domain == "example.com"
        assert e.ttl == config.default_ttl

    def test_multiple_rules(self, config: Config) -> None:
        spec = {
            "rules": [
                {"host": "app.example.com"},
                {"host": "api.example.com"},
            ]
        }
        entries = _build_desired(spec, "10.0.0.1", {}, "my-ingress", "default", config)
        assert len(entries) == 2
        assert {e.fqdn for e in entries} == {"app.example.com", "api.example.com"}

    def test_skips_rules_without_host(self, config: Config) -> None:
        spec = {"rules": [{"http": {}}]}
        entries = _build_desired(spec, "10.0.0.1", {}, "my-ingress", "default", config)
        assert entries == []

    def test_empty_rules(self, config: Config) -> None:
        entries = _build_desired({}, "10.0.0.1", {}, "my-ingress", "default", config)
        assert entries == []

    def test_domain_split_annotation(self, config: Config) -> None:
        spec = {"rules": [{"host": "app.sub.example.com"}]}
        annotations = {"opnsense.io/domain-split": "2"}
        entries = _build_desired(spec, "10.0.0.1", annotations, "my-ingress", "default", config)
        assert entries[0].hostname == "app.sub"
        assert entries[0].domain == "example.com"

    def test_description_includes_namespace_name(self, config: Config) -> None:
        spec = {"rules": [{"host": "app.example.com"}]}
        entries = _build_desired(spec, "10.0.0.1", {}, "my-ingress", "prod", config)
        assert "prod/my-ingress" in entries[0].description
