"""Thợ kiểm tra máy + báo giá sửa chữa chính thức (TUẤN duyệt mẫu M3/M4 14/11/2026, đợt 3).

repair_inspections: một bản kiểm tra cho mỗi phiếu sửa ở xưởng (model đối chiếu bảng giá, ai kiểm, lúc nào, đã áp vào báo giá).
repair_inspection_items: mỗi dịch vụ gói thợ chọn: phương án đã tick, cấp app tự tính, cấp thợ chọn (khác thì có lý do),
linh kiện dự kiến (nội bộ), PA chính hãng, mô tả kết quả cho khách (Codex trên Z2 soạn sẵn, nhân viên sửa; TUẤN 14/11). Giá không lưu ở đây: áp báo giá thì ghi thành dòng PHI của phiếu.

Revision ID: 0095
Revises: 0094
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0095'
down_revision = '0094'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('repair_inspections',
        sa.Column('ticket_id', sa.BigInteger(), sa.ForeignKey('service_tickets.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('model_name', sa.String(length=160), nullable=False, server_default=''),
        sa.Column('model_id', sa.Integer(), sa.ForeignKey('repair_models.id', ondelete='SET NULL'), nullable=True),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('inspected_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('inspected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('applied_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('applied_lines', JSONB(), nullable=False, server_default='[]'),
        sa.Column('warranty_months', sa.Integer(), nullable=True),
        sa.Column('ai_job_id', sa.BigInteger(), sa.ForeignKey('zalo_ai_jobs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.create_table('repair_inspection_items',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('ticket_id', sa.BigInteger(), sa.ForeignKey('repair_inspections.ticket_id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('service_id', sa.Integer(), sa.ForeignKey('price_services.id'), nullable=False),
        sa.Column('options', JSONB(), nullable=False, server_default='[]'),
        sa.Column('level_auto', sa.Integer(), nullable=False),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('level_reason', sa.Text(), nullable=False, server_default=''),
        sa.Column('parts', JSONB(), nullable=False, server_default='[]'),
        sa.Column('genuine', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('customer_note', sa.Text(), nullable=False, server_default=''),
        sa.Column('sort', sa.Integer(), nullable=False, server_default='0'),
        sa.CheckConstraint('level between 1 and 3 and level_auto between 1 and 3', name='ck_repair_inspection_items_level'),
        sa.CheckConstraint("level = level_auto or btrim(level_reason) <> ''", name='ck_repair_inspection_items_reason'),
        sa.UniqueConstraint('ticket_id', 'service_id', name='uq_repair_inspection_items_service'))


def downgrade():
    op.drop_table('repair_inspection_items')
    op.drop_table('repair_inspections')
