"""Tally operations: report runs and opening balances.

Revision ID: 0002_tally
Revises: 0001_initial

0001 built the whole schema with create_all, so this is the first revision to
declare tables explicitly. Both tables are new; no existing table changes.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_tally"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "tally_runs" not in existing:
        op.create_table(
            "tally_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tool", sa.String(32), nullable=False),
            sa.Column("user_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("company", sa.String(255), server_default=""),
            sa.Column("input_path", sa.String(512), server_default=""),
            sa.Column("file_name", sa.String(255), server_default=""),
            sa.Column("date_from", sa.String(32), server_default=""),
            sa.Column("date_to", sa.String(32), server_default=""),
            sa.Column("locations", sa.Text(), nullable=True),
            sa.Column("accounts", sa.Text(), nullable=True),
            sa.Column("prefix", sa.String(16), server_default="DS"),
            sa.Column("receipt_type", sa.String(32), server_default="Deposit"),
            sa.Column("status", sa.String(32), server_default="completed"),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )

    if "tally_opening_balances" not in existing:
        op.create_table(
            "tally_opening_balances",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("company", sa.String(255), nullable=False),
            sa.Column("as_of", sa.String(32), nullable=False),
            sa.Column("location", sa.String(128), nullable=False),
            sa.Column("account", sa.String(255), nullable=False),
            sa.Column("amount", sa.String(32), nullable=False),
            sa.Column("updated_by", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint("company", "as_of", "location", "account",
                                name="uq_tally_opening"),
        )


def downgrade() -> None:
    op.drop_table("tally_opening_balances")
    op.drop_table("tally_runs")
