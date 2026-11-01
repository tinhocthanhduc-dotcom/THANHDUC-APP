"""Trợ lý AI (TUẤN 01/11/2026): khóa riêng cho Claude trên máy chủ và hàng việc AI đề xuất/đã làm.

AI không có tài khoản người dùng, không ghi thẳng nghiệp vụ: chỉ nhắc việc, ghi chú,
và đề xuất; việc giao thợ/đổi hẹn chỉ chạy khi giám đốc bấm Duyệt (chạy bằng tài khoản giám đốc).

Revision ID: 0070
Revises: 0069
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0070'
down_revision = '0069'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_keys',
        sa.Column('id', sa.Integer(), primary_key=True),
        # Mỗi AI một khóa, phạm vi riêng: 'dieu-phoi' (Claude điều phối), 'dang-web' (Codex đăng web, đợt sau).
        sa.Column('agent', sa.String(16), nullable=False),
        sa.Column('scopes', sa.ARRAY(sa.String(16)), nullable=False, server_default='{}'),
        sa.Column('prefix', sa.String(16), nullable=False),
        sa.Column('key_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('paused', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.Column('revoked_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('last_used_at', sa.DateTime(timezone=True)),
    )
    # Mỗi AI chỉ một khóa còn hiệu lực.
    op.create_index('uq_ai_keys_active', 'ai_keys', ['agent'], unique=True, postgresql_where=sa.text('revoked_at is null'))
    op.create_table('ai_actions',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False, server_default=''),
        sa.Column('target_user_id', sa.Integer(), sa.ForeignKey('users.id'), index=True),
        sa.Column('ticket_id', sa.BigInteger(), sa.ForeignKey('service_tickets.id')),
        sa.Column('payload', JSONB(), nullable=False, server_default='{}'),
        sa.Column('key_id', sa.Integer(), sa.ForeignKey('ai_keys.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
        sa.Column('expires_at', sa.DateTime(timezone=True)),
        sa.Column('decided_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('decided_at', sa.DateTime(timezone=True)),
        sa.Column('result', sa.Text(), nullable=False, server_default=''),
        sa.CheckConstraint("kind in ('NHAC_VIEC','GHI_CHU','BAO_CAO','GIAO_VIEC','DOI_HEN','DE_XUAT_NHAP')", name='ck_ai_actions_kind'),
        sa.CheckConstraint("status in ('DA_GUI','CHO_DUYET','DA_DUYET','TU_CHOI','HET_HAN','LOI')", name='ck_ai_actions_status'),
        sa.CheckConstraint("kind <> 'NHAC_VIEC' or target_user_id is not null", name='ck_ai_actions_remind_target'),
        sa.CheckConstraint("kind not in ('GIAO_VIEC','DOI_HEN') or ticket_id is not null", name='ck_ai_actions_ticket'),
    )
    op.create_index('ix_ai_actions_status', 'ai_actions', ['status', 'id'])


def downgrade():
    op.drop_table('ai_actions')
    op.drop_table('ai_keys')
