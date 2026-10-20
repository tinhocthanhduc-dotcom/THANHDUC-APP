"""TK: employee accounts and revocable sessions.

Revision ID: 0037
Revises: 0036
"""
from alembic import op
import sqlalchemy as sa

revision = '0037'
down_revision = '0036'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users',sa.Column('phone',sa.Text(),nullable=False,server_default=''))
    op.add_column('users',sa.Column('token_version',sa.Integer(),nullable=False,server_default='1'))
    op.add_column('users',sa.Column('must_change_password',sa.Boolean(),nullable=False,server_default=sa.text('false')))
    op.add_column('users',sa.Column('last_login_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('users',sa.Column('updated_at',sa.DateTime(timezone=True),nullable=True))


def downgrade():
    for column in ('updated_at','last_login_at','must_change_password','token_version','phone'):
        op.drop_column('users',column)
