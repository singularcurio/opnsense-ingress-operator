from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from opnsense_py import OPNsenseClient
from opnsense_py.models.unbound import HostOverride

from ingress_operator.config import Config

logger = logging.getLogger(__name__)


@dataclass
class HostEntry:
    """A desired DNS host override derived from an Ingress rule."""

    fqdn: str
    ip: str
    hostname: str
    domain: str
    ttl: int
    description: str


def split_fqdn(fqdn: str, domain_split: int = 1) -> tuple[str, str]:
    """Split an FQDN into (hostname, domain) parts.

    domain_split controls how many labels from the right form the domain.
    Default of 1 splits on the first dot: app.example.com → (app, example.com).
    A value of 2 would give: app.sub.example.com → (app.sub, example.com).
    """
    parts = fqdn.split(".")
    if len(parts) <= domain_split:
        return fqdn, ""
    hostname = ".".join(parts[:domain_split])
    domain = ".".join(parts[domain_split:])
    return hostname, domain


def load_uuid_map(annotations: dict[str, str], uuids_annotation: str) -> dict[str, str]:
    """Load the FQDN→UUID map from the Ingress annotation. Returns {} on missing/invalid."""
    raw = annotations.get(uuids_annotation, "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in annotation %s, treating as empty", uuids_annotation)
        return {}


def sync_host_overrides(
    client: OPNsenseClient,
    config: Config,
    desired: list[HostEntry],
    current_uuids: dict[str, str],
) -> dict[str, str]:
    """Diff and apply host override changes. Returns updated FQDN→UUID map.

    - Creates entries that exist in desired but not in current_uuids
    - Updates entries where the IP has changed (by deleting and re-creating)
    - Deletes entries that exist in current_uuids but not in desired
    - Calls reconfigure() only if any changes were made
    """
    desired_map = {e.fqdn: e for e in desired}
    changed = False
    new_uuids: dict[str, str] = {}

    # Create or update
    for fqdn, entry in desired_map.items():
        existing_uuid = current_uuids.get(fqdn)
        if existing_uuid:
            # Check if IP needs updating
            try:
                current = client.unbound.get_host_override(existing_uuid)
                if current.server == entry.ip:
                    new_uuids[fqdn] = existing_uuid
                    logger.debug("No change for %s (%s)", fqdn, entry.ip)
                    continue
                logger.info("Updating %s: %s → %s", fqdn, current.server, entry.ip)
                client.unbound.set_host_override(
                    existing_uuid, _build_override(entry, config)
                )
                new_uuids[fqdn] = existing_uuid
                changed = True
            except Exception:
                logger.warning(
                    "Could not fetch existing override for %s (uuid=%s), recreating",
                    fqdn,
                    existing_uuid,
                )
                new_uuids[fqdn] = _create_override(client, entry, config)
                changed = True
        else:
            logger.info("Creating override for %s → %s", fqdn, entry.ip)
            new_uuids[fqdn] = _create_override(client, entry, config)
            changed = True

    # Delete stale entries
    for fqdn, uuid in current_uuids.items():
        if fqdn not in desired_map:
            logger.info("Deleting stale override for %s (uuid=%s)", fqdn, uuid)
            try:
                client.unbound.del_host_override(uuid)
                changed = True
            except Exception:
                logger.warning("Failed to delete override %s for %s", uuid, fqdn)

    if changed:
        logger.info("Applying Unbound reconfigure")
        client.unbound.reconfigure()

    return new_uuids


def delete_host_overrides(
    client: OPNsenseClient,
    uuids: dict[str, str],
) -> None:
    """Delete all host overrides in the UUID map and reconfigure."""
    if not uuids:
        return
    for fqdn, uuid in uuids.items():
        logger.info("Deleting override for %s (uuid=%s)", fqdn, uuid)
        try:
            client.unbound.del_host_override(uuid)
        except Exception:
            logger.warning("Failed to delete override %s for %s", uuid, fqdn)
    client.unbound.reconfigure()


def reconcile_orphans(
    client: OPNsenseClient,
    known_uuids: set[str],
    description_prefix: str,
) -> None:
    """Delete any OPNsense host overrides that were created by this operator
    but are no longer referenced by any Ingress annotation.

    Identification: the override's description starts with description_prefix.
    Safety: only deletes entries whose UUID is absent from known_uuids.
    """
    response = client.unbound.search_host_overrides()
    orphans = [
        override
        for override in response.rows
        if (override.description or "").startswith(description_prefix)
        and getattr(override, "uuid", None) not in known_uuids
    ]

    if not orphans:
        logger.debug("Reconcile: no orphaned overrides found")
        return

    for override in orphans:
        uuid = getattr(override, "uuid", None)
        logger.info(
            "Reconcile: deleting orphaned override %s (uuid=%s)",
            f"{override.hostname}.{override.domain}",
            uuid,
        )
        try:
            client.unbound.del_host_override(uuid)
        except Exception:
            logger.warning("Reconcile: failed to delete override uuid=%s", uuid)

    client.unbound.reconfigure()
    logger.info("Reconcile: removed %d orphaned override(s)", len(orphans))


def _build_override(entry: HostEntry, config: Config) -> HostOverride:
    return HostOverride(
        enabled="1",
        hostname=entry.hostname,
        domain=entry.domain,
        rr="A",
        server=entry.ip,
        ttl=entry.ttl,
        description=entry.description,
    )


def _create_override(client: OPNsenseClient, entry: HostEntry, config: Config) -> str:
    result = client.unbound.add_host_override(_build_override(entry, config))
    if not result.uuid:
        raise RuntimeError(f"OPNsense returned no UUID when creating override for {entry.fqdn}")
    return result.uuid
