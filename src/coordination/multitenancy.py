"""Multi-tenancy management for the coordination layer."""

from __future__ import annotations

from uuid import UUID

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Quota limit field mapping
# ---------------------------------------------------------------------------
_QUOTA_FIELD_MAP: dict[str, str] = {
    "items": "max_concurrent_items",
    "claims": "max_claims",
    "questions": "max_questions",
    "consolidations": "consolidation_rate_limit",
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
class TenantConfig(BaseModel):
    """Configuration for a single tenant."""

    tenant_id: str
    display_name: str = ""
    active: bool = True
    max_concurrent_items: int = 50
    max_claims: int = 100
    max_questions: int = 50
    consolidation_rate_limit: int = 10  # max concurrent consolidations


# ---------------------------------------------------------------------------
# TenantRouter
# ---------------------------------------------------------------------------
class TenantRouter:
    """Routes requests to the correct tenant."""

    def __init__(self) -> None:
        self._tenants: dict[str, TenantConfig] = {}

    def register_tenant(self, config: TenantConfig) -> None:
        """Register a new tenant configuration."""
        self._tenants[config.tenant_id] = config
        logger.info(
            "tenant_registered",
            tenant_id=config.tenant_id,
            display_name=config.display_name,
        )

    def get_tenant(self, tenant_id: str) -> TenantConfig | None:
        """Retrieve tenant configuration, or *None* if not found."""
        return self._tenants.get(tenant_id)

    def route(self, tenant_id: str) -> str:
        """Return *tenant_id* when valid; raise :class:`ValueError` otherwise."""
        config = self._tenants.get(tenant_id)
        if config is None:
            raise ValueError(f"Unknown tenant: {tenant_id}")
        if not config.active:
            raise ValueError(f"Tenant is inactive: {tenant_id}")
        return tenant_id

    def list_tenants(self) -> list[str]:
        """Return a list of all registered tenant IDs."""
        return list(self._tenants.keys())


# ---------------------------------------------------------------------------
# NamespaceIsolator
# ---------------------------------------------------------------------------
class NamespaceIsolator:
    """Enforces strict namespace boundaries between tenants."""

    def __init__(self, router: TenantRouter) -> None:
        self._router = router
        self._tenant_map: dict[UUID, str] = {}

    def validate_access(self, tenant_id: str, resource_tenant_id: str) -> bool:
        """Return *True* only when the caller owns the resource (strict isolation)."""
        allowed = tenant_id == resource_tenant_id
        if not allowed:
            logger.warning(
                "access_denied",
                tenant_id=tenant_id,
                resource_tenant_id=resource_tenant_id,
            )
        return allowed

    def filter_items(self, tenant_id: str, items: list) -> list:
        """Filter *items* to only those belonging to *tenant_id*.

        Items are matched by looking up their ``id`` in the internal tenant
        map.  If an item's ID is not tracked, it is excluded.
        """
        filtered: list = []
        for item in items:
            item_id: UUID | None = getattr(item, "id", None)
            if item_id is not None and self._tenant_map.get(item_id) == tenant_id:
                filtered.append(item)
        return filtered

    def register_item(self, tenant_id: str, item_id: UUID) -> None:
        """Record that *tenant_id* owns *item_id*."""
        self._tenant_map[item_id] = tenant_id
        logger.debug(
            "item_registered",
            tenant_id=tenant_id,
            item_id=str(item_id),
        )

    def get_owner(self, item_id: UUID) -> str | None:
        """Return the tenant that owns *item_id*, or *None*."""
        return self._tenant_map.get(item_id)


# ---------------------------------------------------------------------------
# QuotaManager
# ---------------------------------------------------------------------------
class QuotaManager:
    """Enforces per-tenant resource quotas."""

    def __init__(self, router: TenantRouter) -> None:
        self._router = router
        self._usage: dict[str, dict[str, int]] = {}

    # -- internal helpers ---------------------------------------------------

    def _ensure_usage(self, tenant_id: str) -> dict[str, int]:
        """Return (and lazily create) the usage record for a tenant."""
        if tenant_id not in self._usage:
            self._usage[tenant_id] = {
                "items": 0,
                "claims": 0,
                "questions": 0,
                "consolidations": 0,
            }
        return self._usage[tenant_id]

    def _get_limit(self, tenant_id: str, resource_type: str) -> int | None:
        """Resolve the quota limit for *resource_type* from tenant config."""
        config = self._router.get_tenant(tenant_id)
        if config is None:
            return None
        field = _QUOTA_FIELD_MAP.get(resource_type)
        if field is None:
            return None
        return getattr(config, field, None)

    # -- public API ---------------------------------------------------------

    def check_quota(self, tenant_id: str, resource_type: str) -> bool:
        """Return *True* if *tenant_id* has quota remaining for *resource_type*."""
        limit = self._get_limit(tenant_id, resource_type)
        if limit is None:
            return False
        usage = self._ensure_usage(tenant_id)
        return usage.get(resource_type, 0) < limit

    def consume(self, tenant_id: str, resource_type: str, amount: int = 1) -> bool:
        """Consume *amount* of quota.  Returns *False* if it would exceed the limit."""
        limit = self._get_limit(tenant_id, resource_type)
        if limit is None:
            return False
        usage = self._ensure_usage(tenant_id)
        current = usage.get(resource_type, 0)
        if current + amount > limit:
            logger.warning(
                "quota_exceeded",
                tenant_id=tenant_id,
                resource_type=resource_type,
                current=current,
                requested=amount,
                limit=limit,
            )
            return False
        usage[resource_type] = current + amount
        logger.debug(
            "quota_consumed",
            tenant_id=tenant_id,
            resource_type=resource_type,
            amount=amount,
            new_total=usage[resource_type],
        )
        return True

    def release(self, tenant_id: str, resource_type: str, amount: int = 1) -> None:
        """Release *amount* of previously consumed quota."""
        usage = self._ensure_usage(tenant_id)
        current = usage.get(resource_type, 0)
        usage[resource_type] = max(0, current - amount)
        logger.debug(
            "quota_released",
            tenant_id=tenant_id,
            resource_type=resource_type,
            amount=amount,
            new_total=usage[resource_type],
        )

    def get_usage(self, tenant_id: str) -> dict[str, int]:
        """Return a copy of the current usage counters for *tenant_id*."""
        return dict(self._ensure_usage(tenant_id))
