"""V4 financial invariant (1.29): DB-enforced commission split totals +
unique active reservation per unit (defense in depth beyond app logic)."""

from __future__ import annotations

from alembic import op

revision: str = "e5b74caf3344"
down_revision: str | None = "5132d7124e47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- V4 1.29: commission splits must total exactly 100.00 per deal ---
    op.execute("""
        CREATE OR REPLACE FUNCTION check_commission_split_total() RETURNS trigger AS $$
        DECLARE
            total NUMERIC;
        BEGIN
            SELECT COALESCE(SUM(share_percentage), 0) INTO total
            FROM commission_splits WHERE deal_id = NEW.deal_id;
            IF total <> 100.00 THEN
                RAISE EXCEPTION 'commission splits for deal % total % (must be exactly 100.00)',
                    NEW.deal_id, total;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_commission_split_total ON commission_splits")
    op.execute("""
        CREATE TRIGGER trg_commission_split_total
        AFTER INSERT OR UPDATE ON commission_splits
        FOR EACH ROW EXECUTE FUNCTION check_commission_split_total();
    """)
    # remainder rule: explicit finalize function — agency absorbs rounding remainder
    op.execute("""
        CREATE OR REPLACE FUNCTION finalize_commission_splits(
            p_deal_id UUID, p_agency_role TEXT DEFAULT 'agency'
        ) RETURNS void AS $$
        DECLARE
            total NUMERIC;
        BEGIN
            SELECT COALESCE(SUM(share_percentage), 0) INTO total
            FROM commission_splits WHERE deal_id = p_deal_id;
            IF total <> 100.00 THEN
                UPDATE commission_splits
                SET share_percentage = share_percentage + (100.00 - total)
                WHERE deal_id = p_deal_id AND party_role = p_agency_role;
            END IF;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # --- V4 3.4: DB-level guard — one ACTIVE reservation per unit ---
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_reservation_per_unit
        ON reservations (asset_id) WHERE status IN ('ACTIVE', 'CONFIRMED');
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_one_active_reservation_per_unit")
    op.execute("DROP FUNCTION IF EXISTS finalize_commission_splits(UUID, TEXT)")
    op.execute("DROP TRIGGER IF EXISTS trg_commission_split_total ON commission_splits")
    op.execute("DROP FUNCTION IF EXISTS check_commission_split_total()")
