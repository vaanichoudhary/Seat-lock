"""add check constraints and status defaults

Revision ID: 98e182f6fdc4
Revises: 2a31840e79c1
Create Date: 2026-10-27 11:13:10.209681

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '98e182f6fdc4'
down_revision: Union[str, Sequence[str], None] = '2a31840e79c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # defaults
    op.alter_column("seats", "status", server_default="available")
    op.alter_column("bookings", "status", server_default="pending")
    op.alter_column("payments", "status", server_default="pending")
    op.alter_column("users", "role", server_default="user")

    # allowed values
    op.create_check_constraint("ck_seats_status", "seats",
        "status IN ('available', 'held', 'booked')")
    op.create_check_constraint("ck_bookings_status", "bookings",
        "status IN ('pending', 'confirmed', 'failed', 'expired', 'cancelled')")
    op.create_check_constraint("ck_payments_status", "payments",
        "status IN ('pending', 'succeeded', 'failed')")
    op.create_check_constraint("ck_users_role", "users",
        "role IN ('user', 'organizer', 'admin')")

    # a held seat must have an owner and an expiry
    op.create_check_constraint("ck_seats_held_has_owner_and_expiry", "seats",
        "status <> 'held' OR (held_by_user_id IS NOT NULL AND held_until IS NOT NULL)")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_seats_held_has_owner_and_expiry", "seats", type_="check")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_constraint("ck_payments_status", "payments", type_="check")
    op.drop_constraint("ck_bookings_status", "bookings", type_="check")
    op.drop_constraint("ck_seats_status", "seats", type_="check")
    for table, col in [("users", "role"), ("payments", "status"),
                       ("bookings", "status"), ("seats", "status")]:
        op.alter_column(table, col, server_default=None)