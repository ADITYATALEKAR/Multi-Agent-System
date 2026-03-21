"""003: Create delta_log_archive table (v3.3 Fix 5).

Three-tier DeltaLog lifecycle: HOT -> WARM -> COLD.
Archive table stores compressed operations for warm/cold tiers.

Revision ID: 003
Revises: 002
Create Date: 2026-03-19
"""

from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE delta_log_archive (
            delta_id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            sequence_number BIGINT NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            operations_compressed BYTEA NOT NULL,
            scope UUID[]
        ) PARTITION BY LIST (tenant_id);

        -- Default partition
        CREATE TABLE delta_log_archive_default PARTITION OF delta_log_archive DEFAULT;

        -- Indexes
        CREATE INDEX idx_dla_tenant_seq ON delta_log_archive (tenant_id, sequence_number);
        CREATE INDEX idx_dla_timestamp ON delta_log_archive (tenant_id, timestamp);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS delta_log_archive CASCADE;")
