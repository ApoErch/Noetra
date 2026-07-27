"""drop_code_entity_trgm_index

Revision ID: 7a96b345a5d0
Revises: 7c1a4b9e2d33
Create Date: 2026-07-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7a96b345a5d0'
down_revision: Union[str, Sequence[str], None] = '7c1a4b9e2d33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Structural retrieval (core/retrieval/structural.py) was removed after the M5 eval
    # ablation showed it moving recall@5 by only +0.04 (docs/RETRIEVAL.md). This trigram
    # GIN index was its only reader — code_entity(repository_id, name) already covers the
    # symbol lookups chunking and the M6 repo map still need. pg_trgm the extension is left
    # enabled since dropping it is a separate, riskier call nothing currently forces.
    op.execute("DROP INDEX IF EXISTS ix_code_entities_name_trgm")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("CREATE INDEX ix_code_entities_name_trgm ON code_entities USING gin (name gin_trgm_ops)")
