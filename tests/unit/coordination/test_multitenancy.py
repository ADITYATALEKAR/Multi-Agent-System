from __future__ import annotations

from uuid import uuid4

import pytest

from src.coordination.multitenancy import NamespaceIsolator, QuotaManager, TenantConfig, TenantRouter


# ── helpers ──────────────────────────────────────────────────────────────

def _make_router_with_tenant(tenant_id: str = "t1", active: bool = True) -> TenantRouter:
    router = TenantRouter()
    router.register_tenant(TenantConfig(tenant_id=tenant_id, display_name="Test", active=active))
    return router


# ── TenantRouter ─────────────────────────────────────────────────────────

def test_register_and_get_tenant():
    router = TenantRouter()
    config = TenantConfig(tenant_id="acme", display_name="Acme Corp")
    router.register_tenant(config)
    result = router.get_tenant("acme")
    assert result is not None
    assert result.tenant_id == "acme"
    assert result.display_name == "Acme Corp"


def test_route_valid_tenant():
    router = _make_router_with_tenant("t1")
    assert router.route("t1") == "t1"


def test_route_unknown_tenant_raises():
    router = TenantRouter()
    with pytest.raises(ValueError, match="Unknown tenant"):
        router.route("nonexistent")


def test_route_inactive_tenant_raises():
    router = _make_router_with_tenant("t_inactive", active=False)
    with pytest.raises(ValueError, match="inactive"):
        router.route("t_inactive")


# ── NamespaceIsolator ────────────────────────────────────────────────────

def test_namespace_isolation_same_tenant():
    """Same tenant can access its own resources."""
    router = _make_router_with_tenant("t1")
    ns = NamespaceIsolator(router)
    assert ns.validate_access("t1", "t1") is True


def test_namespace_isolation_different_tenant():
    """Different tenant is denied access."""
    router = _make_router_with_tenant("t1")
    ns = NamespaceIsolator(router)
    assert ns.validate_access("t1", "t2") is False


def test_register_and_get_owner():
    """Registering an item records ownership; get_owner retrieves it."""
    router = _make_router_with_tenant("t1")
    ns = NamespaceIsolator(router)
    item_id = uuid4()
    ns.register_item("t1", item_id)
    assert ns.get_owner(item_id) == "t1"


# ── QuotaManager ─────────────────────────────────────────────────────────

def test_quota_check_available():
    """check_quota returns True when under limit."""
    router = _make_router_with_tenant("t1")
    qm = QuotaManager(router)
    assert qm.check_quota("t1", "items") is True


def test_quota_consume_and_release():
    """Consuming and releasing quota adjusts usage correctly."""
    router = _make_router_with_tenant("t1")
    qm = QuotaManager(router)

    assert qm.consume("t1", "items", 5) is True
    usage = qm.get_usage("t1")
    assert usage["items"] == 5

    qm.release("t1", "items", 3)
    usage = qm.get_usage("t1")
    assert usage["items"] == 2


def test_quota_exceed_limit():
    """Consuming beyond the limit is rejected."""
    router = _make_router_with_tenant("t1")
    qm = QuotaManager(router)
    # Default max_concurrent_items is 50
    assert qm.consume("t1", "items", 50) is True
    assert qm.consume("t1", "items", 1) is False
