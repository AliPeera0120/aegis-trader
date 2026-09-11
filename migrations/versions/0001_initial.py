"""Initial durable records, market bars, orders and hash-chained audit."""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "records",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_records_kind", "records", ["kind"])
    op.create_index("ix_records_mode", "records", ["mode"])
    op.create_table(
        "orders",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("candidate_id", sa.String(96), unique=True, nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("broker_id", sa.String(96)),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "audit_events",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.String(40), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "controls",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "bars",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("symbol", sa.String(20)),
        sa.Column("start", sa.String(40)),
        sa.Column("timeframe", sa.String(16)),
        sa.Column("source", sa.String(24)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("symbol", "start", "timeframe", "source"),
    )
    op.create_index("ix_bars_symbol", "bars", ["symbol"])
    op.create_index("ix_bars_start", "bars", ["start"])
    op.execute("INSERT INTO controls (key, payload) VALUES ('audit_lock', '{\"n\": 0}')")


def downgrade():
    for table in ["bars", "controls", "audit_events", "orders", "records"]:
        op.drop_table(table)
