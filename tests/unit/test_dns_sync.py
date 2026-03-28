from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ingress_operator.config import Config
from ingress_operator.dns.sync import (
    HostEntry,
    delete_host_overrides,
    load_uuid_map,
    reconcile_orphans,
    split_fqdn,
    sync_host_overrides,
)


@pytest.fixture
def config() -> Config:
    return Config(
        host="opnsense.test",
        api_key="key",
        api_secret="secret",
        annotation_prefix="opnsense.io",
    )


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.unbound = MagicMock()
    return client


def make_entry(fqdn: str = "app.example.com", ip: str = "10.0.0.1") -> HostEntry:
    return HostEntry(
        fqdn=fqdn,
        ip=ip,
        hostname="app",
        domain="example.com",
        ttl=300,
        description="managed by operator | default/my-ingress",
    )


class TestSplitFqdn:
    def test_default_split(self) -> None:
        assert split_fqdn("app.example.com") == ("app", "example.com")

    def test_subdomain(self) -> None:
        assert split_fqdn("app.sub.example.com") == ("app", "sub.example.com")

    def test_domain_split_2(self) -> None:
        assert split_fqdn("app.sub.example.com", domain_split=2) == ("app.sub", "example.com")

    def test_single_label(self) -> None:
        assert split_fqdn("localhost") == ("localhost", "")

    def test_two_labels(self) -> None:
        assert split_fqdn("host.local") == ("host", "local")


class TestLoadUuidMap:
    def test_empty_annotation(self) -> None:
        assert load_uuid_map({}, "opnsense.io/host-override-uuids") == {}

    def test_valid_json(self) -> None:
        annotations = {"opnsense.io/host-override-uuids": '{"app.example.com": "uuid-1"}'}
        result = load_uuid_map(annotations, "opnsense.io/host-override-uuids")
        assert result == {"app.example.com": "uuid-1"}

    def test_invalid_json_returns_empty(self) -> None:
        annotations = {"opnsense.io/host-override-uuids": "not-json"}
        result = load_uuid_map(annotations, "opnsense.io/host-override-uuids")
        assert result == {}


class TestSyncHostOverrides:
    def test_creates_new_override(self, mock_client: MagicMock, config: Config) -> None:
        mock_client.unbound.add_host_override.return_value = MagicMock(uuid="new-uuid")
        entry = make_entry()

        result = sync_host_overrides(mock_client, config, [entry], {})

        mock_client.unbound.add_host_override.assert_called_once()
        mock_client.unbound.reconfigure.assert_called_once()
        assert result == {"app.example.com": "new-uuid"}

    def test_no_change_skips_update(self, mock_client: MagicMock, config: Config) -> None:
        existing = MagicMock()
        existing.server = "10.0.0.1"
        mock_client.unbound.get_host_override.return_value = existing
        entry = make_entry(ip="10.0.0.1")

        result = sync_host_overrides(mock_client, config, [entry], {"app.example.com": "existing-uuid"})

        mock_client.unbound.set_host_override.assert_not_called()
        mock_client.unbound.reconfigure.assert_not_called()
        assert result == {"app.example.com": "existing-uuid"}

    def test_updates_when_ip_changes(self, mock_client: MagicMock, config: Config) -> None:
        existing = MagicMock()
        existing.server = "10.0.0.1"
        mock_client.unbound.get_host_override.return_value = existing
        entry = make_entry(ip="10.0.0.2")

        result = sync_host_overrides(mock_client, config, [entry], {"app.example.com": "existing-uuid"})

        mock_client.unbound.set_host_override.assert_called_once()
        mock_client.unbound.reconfigure.assert_called_once()
        assert result == {"app.example.com": "existing-uuid"}

    def test_deletes_stale_entries(self, mock_client: MagicMock, config: Config) -> None:
        result = sync_host_overrides(
            mock_client, config, [], {"old.example.com": "stale-uuid"}
        )

        mock_client.unbound.del_host_override.assert_called_once_with("stale-uuid")
        mock_client.unbound.reconfigure.assert_called_once()
        assert result == {}

    def test_no_changes_skips_reconfigure(self, mock_client: MagicMock, config: Config) -> None:
        result = sync_host_overrides(mock_client, config, [], {})

        mock_client.unbound.reconfigure.assert_not_called()
        assert result == {}


class TestDeleteHostOverrides:
    def test_deletes_all_and_reconfigures(self, mock_client: MagicMock) -> None:
        uuids = {"app.example.com": "uuid-1", "api.example.com": "uuid-2"}
        delete_host_overrides(mock_client, uuids)

        assert mock_client.unbound.del_host_override.call_count == 2
        mock_client.unbound.reconfigure.assert_called_once()

    def test_empty_uuids_is_noop(self, mock_client: MagicMock) -> None:
        delete_host_overrides(mock_client, {})

        mock_client.unbound.del_host_override.assert_not_called()
        mock_client.unbound.reconfigure.assert_not_called()


def _make_override(uuid: str, hostname: str, description: str) -> MagicMock:
    o = MagicMock()
    o.uuid = uuid
    o.hostname = hostname
    o.domain = "example.com"
    o.description = description
    return o


class TestReconcileOrphans:
    PREFIX = "managed by opnsense-ingress-operator"

    def test_deletes_orphaned_overrides(self, mock_client: MagicMock) -> None:
        orphan = _make_override("orphan-uuid", "old", f"{self.PREFIX} | default/deleted-ingress")
        mock_client.unbound.search_host_overrides.return_value = MagicMock(rows=[orphan])

        reconcile_orphans(mock_client, known_uuids=set(), description_prefix=self.PREFIX)

        mock_client.unbound.del_host_override.assert_called_once_with("orphan-uuid")
        mock_client.unbound.reconfigure.assert_called_once()

    def test_skips_known_uuids(self, mock_client: MagicMock) -> None:
        override = _make_override("live-uuid", "app", f"{self.PREFIX} | default/live-ingress")
        mock_client.unbound.search_host_overrides.return_value = MagicMock(rows=[override])

        reconcile_orphans(mock_client, known_uuids={"live-uuid"}, description_prefix=self.PREFIX)

        mock_client.unbound.del_host_override.assert_not_called()
        mock_client.unbound.reconfigure.assert_not_called()

    def test_skips_unmanaged_overrides(self, mock_client: MagicMock) -> None:
        external = _make_override("ext-uuid", "manual", "manually created entry")
        mock_client.unbound.search_host_overrides.return_value = MagicMock(rows=[external])

        reconcile_orphans(mock_client, known_uuids=set(), description_prefix=self.PREFIX)

        mock_client.unbound.del_host_override.assert_not_called()
        mock_client.unbound.reconfigure.assert_not_called()

    def test_mixed_orphan_and_known(self, mock_client: MagicMock) -> None:
        orphan = _make_override("orphan-uuid", "old", f"{self.PREFIX} | default/gone")
        live = _make_override("live-uuid", "app", f"{self.PREFIX} | default/live")
        mock_client.unbound.search_host_overrides.return_value = MagicMock(rows=[orphan, live])

        reconcile_orphans(mock_client, known_uuids={"live-uuid"}, description_prefix=self.PREFIX)

        mock_client.unbound.del_host_override.assert_called_once_with("orphan-uuid")
        mock_client.unbound.reconfigure.assert_called_once()

    def test_no_overrides_is_noop(self, mock_client: MagicMock) -> None:
        mock_client.unbound.search_host_overrides.return_value = MagicMock(rows=[])

        reconcile_orphans(mock_client, known_uuids=set(), description_prefix=self.PREFIX)

        mock_client.unbound.del_host_override.assert_not_called()
        mock_client.unbound.reconfigure.assert_not_called()
