"""AI soạn sẵn câu trả lời + Trả lời tự động Zalo (đợt 2–3 zalo.quanlythanhduc.com, TUẤN duyệt mẫu 19/11/2026).

Mỗi lần nhân viên bấm "AI soạn câu trả lời" là một dòng: hội thoại, lượt hỏi máy Z2, bản AI soạn,
người bấm (trống = Trả lời tự động soạn), đã gửi hay bỏ. AI không tự gửi; người duyệt sửa rồi bấm Gửi khách.
Trả lời tự động: thêm cột vào zalo_tro_ly (công tắc chung mặc định TẮT + mức cho từng loại tin). Không đổi dữ liệu cũ.

Revision ID: 0109
Revises: 0108
"""
from alembic import op
import sqlalchemy as sa

revision = '0109'
down_revision = '0108'
branch_labels = None
depends_on = None
RULES = (('auto_ngoai_gio', 'TU_GUI'), ('auto_tinh_trang', 'TU_GUI'), ('auto_hoi_gia', 'CHO_DUYET'),
         ('auto_bao_hong', 'TU_GUI'), ('auto_nguoi_la', 'TU_GUI'))


def upgrade():
    op.create_table(
        'zalo_reply_drafts',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('chat_pk', sa.Integer(), sa.ForeignKey('zalo_chats.id'), nullable=False),
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('zalo_ai_jobs.id'), nullable=True),
        sa.Column('status', sa.String(8), nullable=False, server_default='DANG'),
        sa.Column('facts', sa.Text(), nullable=False, server_default=''),
        sa.Column('text', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),  # trống = Trả lời tự động soạn
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('closed_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_message_id', sa.BigInteger(), sa.ForeignKey('zalo_messages.id'), nullable=True),
        sa.CheckConstraint("status in ('DANG','XONG','LOI','DA_GUI','BO')", name='ck_zalo_reply_drafts_status'),
    )
    op.create_index('ix_zalo_reply_drafts_chat', 'zalo_reply_drafts', ['chat_pk', 'id'])
    op.add_column('zalo_tro_ly', sa.Column('auto_on', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    for col, default in RULES:
        op.add_column('zalo_tro_ly', sa.Column(col, sa.String(9), nullable=False, server_default=default))
        allowed = "('CHO_DUYET','TAT')" if col == 'auto_hoi_gia' else "('TU_GUI','CHO_DUYET','TAT')"
        op.create_check_constraint(f'ck_zalo_tro_ly_{col}', 'zalo_tro_ly', f'{col} in {allowed}')


def downgrade():
    for col, _ in RULES:
        op.drop_constraint(f'ck_zalo_tro_ly_{col}', 'zalo_tro_ly', type_='check')
        op.drop_column('zalo_tro_ly', col)
    op.drop_column('zalo_tro_ly', 'auto_on')
    op.drop_index('ix_zalo_reply_drafts_chat', table_name='zalo_reply_drafts')
    op.drop_table('zalo_reply_drafts')
