"""AI soạn sẵn câu trả lời trong Tất cả tin Zalo (đợt 2 zalo.quanlythanhduc.com, TUẤN duyệt mẫu 19/11/2026).

Mỗi lần nhân viên bấm "AI soạn câu trả lời" là một dòng: hội thoại, lượt hỏi máy Z2, bản AI soạn,
người bấm, đã gửi hay bỏ. AI không tự gửi; người duyệt sửa rồi bấm Gửi khách. Bảng mới, không đổi dữ liệu cũ.

Revision ID: 0109
Revises: 0108
"""
from alembic import op
import sqlalchemy as sa

revision = '0109'
down_revision = '0108'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'zalo_reply_drafts',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('chat_pk', sa.Integer(), sa.ForeignKey('zalo_chats.id'), nullable=False),
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('zalo_ai_jobs.id'), nullable=True),
        sa.Column('status', sa.String(8), nullable=False, server_default='DANG'),
        sa.Column('facts', sa.Text(), nullable=False, server_default=''),
        sa.Column('text', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('closed_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_message_id', sa.BigInteger(), sa.ForeignKey('zalo_messages.id'), nullable=True),
        sa.CheckConstraint("status in ('DANG','XONG','LOI','DA_GUI','BO')", name='ck_zalo_reply_drafts_status'),
    )
    op.create_index('ix_zalo_reply_drafts_chat', 'zalo_reply_drafts', ['chat_pk', 'id'])


def downgrade():
    op.drop_index('ix_zalo_reply_drafts_chat', table_name='zalo_reply_drafts')
    op.drop_table('zalo_reply_drafts')
