from __future__ import annotations

import json
import logging
from typing import Any

import kopf
from kubernetes import client as k8s_client
from opnsense_py import OPNsenseClient

from ingress_operator.config import Config
from ingress_operator.dns.sync import (
    HostEntry,
    delete_host_overrides,
    load_uuid_map,
    reconcile_orphans,
    split_fqdn,
    sync_host_overrides,
)

logger = logging.getLogger(__name__)


def _get_ingress_ip(status: dict[str, Any], annotations: dict[str, str], config: Config) -> str | None:
    """Resolve the target IP for this Ingress.

    Priority:
    1. Manual override annotation
    2. status.loadBalancer.ingress[0].ip
    3. status.loadBalancer.ingress[0].hostname (not resolved — not supported)
    """
    override = annotations.get(config.target_ip_annotation)
    if override:
        return override

    lb_ingresses = (
        status.get("loadBalancer", {}).get("ingress") or []
    )
    for lb in lb_ingresses:
        if lb.get("ip"):
            return lb["ip"]

    return None


def _build_desired(
    spec: dict[str, Any],
    ip: str,
    annotations: dict[str, str],
    name: str,
    namespace: str,
    config: Config,
) -> list[HostEntry]:
    """Build the list of desired HostEntry objects from an Ingress spec."""
    domain_split_raw = annotations.get(config.domain_split_annotation, "1")
    try:
        domain_split = int(domain_split_raw)
    except ValueError:
        domain_split = 1

    entries: list[HostEntry] = []
    for rule in spec.get("rules") or []:
        fqdn = rule.get("host", "").strip()
        if not fqdn:
            continue
        hostname, domain = split_fqdn(fqdn, domain_split)
        entries.append(
            HostEntry(
                fqdn=fqdn,
                ip=ip,
                hostname=hostname,
                domain=domain,
                ttl=config.default_ttl,
                description=f"{config.description_prefix} | {namespace}/{name}",
            )
        )
    return entries


def _patch_uuids(patch: kopf.Patch, uuids: dict[str, str], config: Config) -> None:
    patch.metadata.setdefault("annotations", {})
    patch.metadata["annotations"][config.uuids_annotation] = json.dumps(uuids)


def register_handlers(cfg: Config, opnsense: OPNsenseClient) -> None:
    """Register kopf Ingress event handlers. Called once at startup."""

    @kopf.on.create("networking.k8s.io", "v1", "ingresses")
    @kopf.on.update("networking.k8s.io", "v1", "ingresses")
    def on_ingress_create_or_update(
        spec: dict[str, Any],
        status: dict[str, Any],
        meta: kopf.Meta,
        patch: kopf.Patch,
        logger: logging.Logger,
        **_: Any,
    ) -> None:
        name = meta.get("name", "")
        namespace = meta.get("namespace", "")
        annotations: dict[str, str] = dict(meta.get("annotations") or {})

        ip = _get_ingress_ip(status, annotations, cfg)
        if not ip:
            raise kopf.TemporaryError(
                f"No LoadBalancer IP yet for {namespace}/{name}, will retry",
                delay=15,
            )

        desired = _build_desired(spec, ip, annotations, name, namespace, cfg)
        if not desired:
            logger.info("No hostnames found in Ingress %s/%s, skipping", namespace, name)
            return

        current_uuids = load_uuid_map(annotations, cfg.uuids_annotation)
        new_uuids = sync_host_overrides(opnsense, cfg, desired, current_uuids)
        _patch_uuids(patch, new_uuids, cfg)

    @kopf.on.delete("networking.k8s.io", "v1", "ingresses")
    def on_ingress_delete(
        meta: kopf.Meta,
        logger: logging.Logger,
        **_: Any,
    ) -> None:
        name = meta.get("name", "")
        namespace = meta.get("namespace", "")
        annotations: dict[str, str] = dict(meta.get("annotations") or {})

        current_uuids = load_uuid_map(annotations, cfg.uuids_annotation)
        if not current_uuids:
            logger.info("No managed overrides found for %s/%s, nothing to delete", namespace, name)
            return

        delete_host_overrides(opnsense, current_uuids)

    @kopf.on.timer("networking.k8s.io", "v1", "ingresses", interval=cfg.reconcile_interval, initial_delay=60)
    def on_reconcile_timer(
        meta: kopf.Meta,
        logger: logging.Logger,
        **_: Any,
    ) -> None:
        """Periodically scan all Ingresses and remove orphaned OPNsense overrides.

        Runs on every Ingress object individually (kopf timer semantics), but we
        only want one cluster-wide reconcile pass per interval. We gate this by
        only running when the handler fires for the first Ingress seen (lowest
        namespace/name), so the work happens exactly once per cycle regardless of
        how many Ingresses exist.
        """
        name = meta.get("name", "")
        namespace = meta.get("namespace", "")

        # Collect the authoritative set of UUIDs from all Ingresses cluster-wide
        networking = k8s_client.NetworkingV1Api()
        ingresses = networking.list_ingress_for_all_namespaces().items

        # Only run the full reconcile on the lexicographically first Ingress to
        # avoid redundant passes when the timer fires for every object.
        sorted_keys = sorted(
            (f"{i.metadata.namespace}/{i.metadata.name}" for i in ingresses)
        )
        if not sorted_keys or sorted_keys[0] != f"{namespace}/{name}":
            return

        known_uuids: set[str] = set()
        for ingress in ingresses:
            annotations = ingress.metadata.annotations or {}
            uuid_map = load_uuid_map(dict(annotations), cfg.uuids_annotation)
            known_uuids.update(uuid_map.values())

        logger.info(
            "Reconcile: %d Ingress(es), %d known UUID(s)", len(ingresses), len(known_uuids)
        )
        reconcile_orphans(opnsense, known_uuids, cfg.description_prefix)
