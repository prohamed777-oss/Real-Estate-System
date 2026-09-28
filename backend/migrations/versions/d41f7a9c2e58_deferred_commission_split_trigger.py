"""Make the commission-split total check a deferred constraint trigger.

The original trigger was an immediate FOR EACH ROW trigger: it fired after
the FIRST split row was inserted, when SUM(share_percentage) != 100.00 yet,
so any deal with two or more splits always aborted (V4 audit). A DEFERRABLE
INITIALLY DEFERRED constraint trigger enforces exactly the same invariant at
COMMIT, when every row of the transaction exists.

Revision ID: d41f7a9c2e58
Revises: b8e07afg6677
"""

from __future__ import annotations

from alembic import op

revision = "d41f7a9c2e58"
down_revision = "b8e07afg6677"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_commission_split_total ON commission_splits")
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_commission_split_total
        AFTER INSERT OR UPDATE ON commission_splits
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION check_commission_split_total()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_commission_split_total ON commission_splits")
    op.execute("""
        CREATE TRIGGER trg_commission_split_total
        AFTER INSERT OR UPDATE ON commission_splits
        FOR EACH ROW EXECUTE FUNCTION check_commission_split_total()
    """)
