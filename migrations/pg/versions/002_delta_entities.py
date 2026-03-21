"""002: Create delta_entities table (v3.3 Fix 1).

Normalized side table replacing scope[1] index.
Fan-out on append for efficient entity-centric queries.

Revision ID: 002
Revises: 001
Create Date: 2026-03-19
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE delta_entities (
            tenant_id TEXT NOT NULL,
            entity_id UUID NOT NULL,
            sequence_number BIGINT NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            delta_id UUID NOT NULL,
            PRIMARY KEY (tenant_id, entity_id, sequence_number)
        ) PARTITION BY LIST (tenant_id);

        -- Default partition
        CREATE TABLE delta_entities_default PARTITION OF delta_entities DEFAULT;

        -- Indexes (v3.3 Fix 1)
        CREATE INDEX idx_de_entity_time ON delta_entities (tenant_id, entity_id, timestamp);
        CREATE INDEX idx_de_time ON delta_entities (tenant_id, timestamp, sequence_number);
        CREATE INDEX idx_de_delta ON delta_entities (delta_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS delta_entities CASCADE;")
