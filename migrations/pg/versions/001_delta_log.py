"""001: Create delta_log table.

Revision ID: 001
Revises: None
Create Date: 2026-03-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY, TIMESTAMPTZ

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE delta_log (
            delta_id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            sequence_number BIGSERIAL,
            timestamp TIMESTAMPTZ NOT NULL,
            source TEXT NOT NULL,
            operations JSONB NOT NULL,
            scope UUID[] NOT NULL,
            causal_predecessor UUID,
            schema_version INT NOT NULL DEFAULT 1
        ) PARTITION BY LIST (tenant_id);

        -- Default partition for single-tenant dev
        CREATE TABLE delta_log_default PARTITION OF delta_log DEFAULT;

        -- Indexes
        CREATE UNIQUE INDEX idx_dl_tenant_seq ON delta_log (tenant_id, sequence_number);
        CREATE INDEX idx_dl_timestamp ON delta_log (tenant_id, timestamp);
        CREATE INDEX idx_dl_source ON delta_log (source);
        CREATE INDEX idx_dl_causal ON delta_log (causal_predecessor) WHERE causal_predecessor IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS delta_log CASCADE;")
