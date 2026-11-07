"""Initial production schema.

Revision ID: 20261107_0001
Revises: None
"""
from alembic import op
import sqlalchemy as sa


revision = "20261107_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("business_name", sa.Text(), nullable=False),
        sa.Column("contact_name", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("email", sa.Text()),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("segment", sa.Text(), nullable=False),
        sa.Column("lane", sa.Text(), nullable=False),
        sa.Column("items_json", sa.Text(), nullable=False),
        sa.Column("monthly", sa.Integer(), nullable=False),
        sa.Column("setup", sa.Integer(), nullable=False),
        sa.Column("contract_months", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("kyc_status", sa.Text(), nullable=False),
        sa.Column("kyc_verdict", sa.Text()),
        sa.Column("partner_code", sa.Text()),
        sa.Column("consent", sa.Integer(), nullable=False),
        sa.Column("consent_version", sa.Text(), nullable=False),
        sa.Column("profile_json", sa.Text(), nullable=False),
    )
    op.create_index("idx_orders_created_at", "orders", ["created_at"])
    op.create_index("idx_orders_phone", "orders", ["phone"])
    op.create_index("idx_orders_partner", "orders", ["partner_code"])

    op.create_table(
        "decision_plans",
        sa.Column("order_id", sa.Text(), sa.ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("access_hash", sa.Text(), nullable=False),
        sa.Column("accepted_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "activation_tasks",
        sa.Column("order_id", sa.Text(), sa.ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("data_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "activation_events",
        sa.Column("order_id", sa.Text(), sa.ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "plan_revisions",
        sa.Column("order_id", sa.Text(), sa.ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("fingerprint", sa.Text(), primary_key=True),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("tasks_json", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "payment_intents",
        sa.Column("order_id", sa.Text(), sa.ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("reference", sa.Text(), unique=True, nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text()),
        sa.Column("url", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
    )
    op.create_table(
        "provider_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "registry_evidence",
        sa.Column("order_id", sa.Text(), sa.ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "whatsapp_sessions",
        sa.Column("phone", sa.Text(), primary_key=True),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "whatsapp_receipts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "interventions",
        sa.Column("order_id", sa.Text(), sa.ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("assignment", sa.Text(), nullable=False),
        sa.Column("skill", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("cost", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outcome", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "auth_otp_challenges",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumed_at", sa.Text()),
    )
    op.create_index("idx_auth_otp_phone_created", "auth_otp_challenges", ["phone", "created_at"])
    op.create_table(
        "customer_sessions",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.Text()),
    )
    op.create_index("idx_customer_sessions_phone", "customer_sessions", ["phone"])
    op.create_table(
        "order_customers",
        sa.Column("order_id", sa.Text(), sa.ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("phone", sa.Text(), nullable=False),
    )
    op.create_index("idx_order_customers_phone", "order_customers", ["phone"])
    op.create_table(
        "consent_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("notice", sa.Text(), nullable=False),
        sa.Column("order_id", sa.Text(), sa.ForeignKey("orders.id", ondelete="SET NULL")),
        sa.Column("lane", sa.Text()),
        sa.Column("outcome", sa.Text()),
        sa.Column("codes_json", sa.Text(), nullable=False),
    )
    op.create_index("idx_consent_order_time", "consent_events", ["order_id", "occurred_at"])


def downgrade() -> None:
    for table in [
        "consent_events", "order_customers", "customer_sessions", "auth_otp_challenges",
        "interventions", "whatsapp_receipts", "whatsapp_sessions", "registry_evidence",
        "provider_events", "payment_intents", "plan_revisions", "activation_events",
        "activation_tasks", "decision_plans", "orders",
    ]:
        op.drop_table(table)
