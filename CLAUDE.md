# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run a single test file
uv run pytest tests/unit/test_dns_sync.py

# Lint
uv run ruff check .

# Type check
uv run mypy ingress_operator/

# Run locally against a real cluster (requires kubeconfig + OPNsense env vars)
OPNSENSE_HOST=192.168.1.1 \
OPNSENSE_API_KEY=... \
OPNSENSE_API_SECRET=... \
OPNSENSE_VERIFY_SSL=false \
uv run opnsense-ingress-operator
```

## Architecture

**opnsense-ingress-operator** is a Kubernetes operator that watches Ingress objects
cluster-wide and syncs their hostnames as Unbound DNS host overrides on an OPNsense router.

### Layers

1. **`ingress_operator/main.py`** — kopf entry point. `@kopf.on.startup` initialises the
   kubernetes client, builds the `OPNsenseClient`, validates connectivity, and calls
   `register_handlers()`. `@kopf.on.cleanup` closes the HTTP client.

2. **`ingress_operator/config.py`** — `Config` dataclass loaded from environment variables.
   All annotation key names are derived from `annotation_prefix`.

3. **`ingress_operator/handlers/ingress.py`** — kopf event handlers registered via
   `register_handlers(cfg, opnsense)`:
   - `@kopf.on.create` / `@kopf.on.update` — extract IP + hostnames, diff against
     annotation state, call `sync_host_overrides`.
   - `@kopf.on.delete` — call `delete_host_overrides` for all UUIDs in the annotation.
   - `@kopf.on.timer` — periodic orphan reconciliation; gates on the lexicographically
     first Ingress to run exactly once per cycle.

4. **`ingress_operator/dns/sync.py`** — pure sync logic, no kopf dependency:
   - `sync_host_overrides` — diffs desired vs current UUID map, creates/updates/deletes,
     calls `reconfigure()` only when something changed.
   - `delete_host_overrides` — deletes all UUIDs and reconfigures.
   - `reconcile_orphans` — scans OPNsense for overrides whose description starts with
     the operator prefix but whose UUID is absent from the known set.

### State management

UUID state is stored as a JSON annotation on each Ingress object:

```
opnsense.io/host-override-uuids: '{"app.example.com": "uuid-abc", "api.example.com": "uuid-def"}'
```

No external state store is required.

### Per-Ingress annotations

| Annotation | Purpose |
|---|---|
| `opnsense.io/host-override-uuids` | Managed automatically — stores OPNsense UUIDs |
| `opnsense.io/target-ip` | Override the synced IP (e.g. for NodePort clusters) |
| `opnsense.io/domain-split` | Number of hostname labels to use (default: `1`) |

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPNSENSE_HOST` | required | OPNsense host/IP |
| `OPNSENSE_API_KEY` | required | API key |
| `OPNSENSE_API_SECRET` | required | API secret |
| `OPNSENSE_VERIFY_SSL` | `true` | Verify TLS certificate |
| `OPNSENSE_DEFAULT_TTL` | `300` | TTL for created overrides |
| `OPNSENSE_ANNOTATION_PREFIX` | `opnsense.io` | Prefix for all annotations |
| `OPNSENSE_DESCRIPTION_PREFIX` | `managed by opnsense-ingress-operator` | Prefix on Unbound descriptions |
| `OPNSENSE_RECONCILE_INTERVAL` | `300` | Seconds between orphan reconcile passes |

### Testing approach

Tests use `unittest.mock.MagicMock` to mock the `OPNsenseClient`. No live OPNsense
instance or cluster is required for unit tests.
