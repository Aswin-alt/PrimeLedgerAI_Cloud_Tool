"""Initial schema revision."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # create_all via seed is primary path; this revision documents the schema
    bind = op.get_bind()
    from webapp.database import Base
    from webapp import models  # noqa: F401

    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    from webapp.database import Base
    from webapp import models  # noqa: F401

    Base.metadata.drop_all(bind=bind)
