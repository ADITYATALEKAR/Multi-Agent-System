"""004: Create semantic_cache table.

Caches analyzer results keyed by file hash + analyzer version + toolchain.

Revision ID: 004
Revises: 003
Create Date: 2026-03-19
"""

from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE semantic_cache (
            cache_key TEXT PRIMARY KEY,
            file_hash TEXT NOT NULL,
            analyzer_version TEXT NOT NULL,
            toolchain_version TEXT NOT NULL,
            result JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            tenant_id TEXT NOT NULL
        );

        CREATE INDEX idx_sc_file ON semantic_cache (file_hash, analyzer_version);
        CREATE INDEX idx_sc_tenant ON semantic_cache (tenant_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS semantic_cache CASCADE;")
