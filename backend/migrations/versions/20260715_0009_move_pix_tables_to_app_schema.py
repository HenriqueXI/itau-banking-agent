"""Move PIX persistence tables into the shared application schema.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Revision 0008 created these tables without an explicit schema while the
    # banking metadata owns them under `app`. Move existing installations
    # instead of rewriting migration history.
    # Alembic's search_path differs between a fresh compose database and the
    # original PRD-008 installation. In the former, 0008 already created the
    # unqualified names inside `app`; in the latter they live in `public`.
    # Move only the legacy physical tables so both histories reach this head.
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('public.pix_daily_buckets') IS NOT NULL THEN "
        "ALTER TABLE public.pix_daily_buckets SET SCHEMA app; "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('public.pix_transfers') IS NOT NULL THEN "
        "ALTER TABLE public.pix_transfers SET SCHEMA app; "
        "END IF; END $$"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.pix_daily_buckets TO app_runtime")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.pix_transfers TO app_runtime")


def downgrade() -> None:
    op.execute("REVOKE ALL ON app.pix_transfers FROM app_runtime")
    op.execute("REVOKE ALL ON app.pix_daily_buckets FROM app_runtime")
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('app.pix_transfers') IS NOT NULL THEN "
        "ALTER TABLE app.pix_transfers SET SCHEMA public; "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('app.pix_daily_buckets') IS NOT NULL THEN "
        "ALTER TABLE app.pix_daily_buckets SET SCHEMA public; "
        "END IF; END $$"
    )
