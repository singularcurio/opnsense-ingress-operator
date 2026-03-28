from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    host: str
    api_key: str
    api_secret: str
    verify_ssl: bool = True
    default_ttl: int = 300
    annotation_prefix: str = "opnsense.io"
    description_prefix: str = "managed by opnsense-ingress-operator"
    reconcile_interval: int = 300

    @property
    def uuids_annotation(self) -> str:
        return f"{self.annotation_prefix}/host-override-uuids"

    @property
    def target_ip_annotation(self) -> str:
        return f"{self.annotation_prefix}/target-ip"

    @property
    def domain_split_annotation(self) -> str:
        return f"{self.annotation_prefix}/domain-split"

    @classmethod
    def from_env(cls) -> Config:
        host = os.environ.get("OPNSENSE_HOST", "")
        api_key = os.environ.get("OPNSENSE_API_KEY", "")
        api_secret = os.environ.get("OPNSENSE_API_SECRET", "")

        if not host:
            raise ValueError("OPNSENSE_HOST is required")
        if not api_key:
            raise ValueError("OPNSENSE_API_KEY is required")
        if not api_secret:
            raise ValueError("OPNSENSE_API_SECRET is required")

        verify_ssl_raw = os.environ.get("OPNSENSE_VERIFY_SSL", "true").lower()
        verify_ssl = verify_ssl_raw not in ("false", "0", "no")

        default_ttl = int(os.environ.get("OPNSENSE_DEFAULT_TTL", "300"))
        annotation_prefix = os.environ.get("OPNSENSE_ANNOTATION_PREFIX", "opnsense.io")
        description_prefix = os.environ.get(
            "OPNSENSE_DESCRIPTION_PREFIX", "managed by opnsense-ingress-operator"
        )
        reconcile_interval = int(os.environ.get("OPNSENSE_RECONCILE_INTERVAL", "300"))

        return cls(
            host=host,
            api_key=api_key,
            api_secret=api_secret,
            verify_ssl=verify_ssl,
            default_ttl=default_ttl,
            annotation_prefix=annotation_prefix,
            description_prefix=description_prefix,
            reconcile_interval=reconcile_interval,
        )
