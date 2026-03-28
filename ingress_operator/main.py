from __future__ import annotations

import logging

import kopf
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from opnsense_py import OPNsenseClient
from opnsense_py.exceptions import OPNsenseError

from ingress_operator.config import Config
from ingress_operator.handlers.ingress import register_handlers

logger = logging.getLogger(__name__)

_config: Config | None = None
_opnsense: OPNsenseClient | None = None


@kopf.on.startup()
def startup(logger: logging.Logger, **_: object) -> None:
    global _config, _opnsense

    # Configure the kubernetes client (in-cluster when running as a pod,
    # falls back to kubeconfig for local development).
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()

    _config = Config.from_env()
    _opnsense = OPNsenseClient(
        host=_config.host,
        api_key=_config.api_key,
        api_secret=_config.api_secret,
        verify_ssl=_config.verify_ssl,
    )

    try:
        status = _opnsense.unbound.status()
        logger.info("Connected to OPNsense at %s — Unbound status: %s", _config.host, status)
    except OPNsenseError as e:
        raise kopf.PermanentError(f"Failed to connect to OPNsense: {e}") from e

    register_handlers(_config, _opnsense)
    logger.info("opnsense-ingress-operator started")


@kopf.on.cleanup()
def cleanup(logger: logging.Logger, **_: object) -> None:
    global _opnsense
    if _opnsense is not None:
        _opnsense.close()
        logger.info("OPNsense client closed")


def main() -> None:
    kopf.run()


if __name__ == "__main__":
    main()
