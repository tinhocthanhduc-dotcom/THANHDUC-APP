"""s0_chong_bam_hai_lan

Revision ID: 0026
Revises: 0025
"""
from alembic import op
import sqlalchemy as sa


revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('form_submissions',
    sa.Column('key', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('result_url', sa.Text(), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'DANG_XU_LY' and finished_at is null and result_url = '') or (status = 'XONG' and finished_at is not null and result_url <> '')", name='ck_form_submissions_result'),
    sa.CheckConstraint("status in ('DANG_XU_LY','XONG')", name='ck_form_submissions_status'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_index('ix_form_submissions_created_at', 'form_submissions', ['created_at'], unique=False)
    # Chỉ chốt một lần; DELETE vẫn dành cho lỗi form và lệnh purge.
    op.execute("""
        CREATE FUNCTION form_submissions_guard() RETURNS trigger AS $$
        BEGIN
            IF OLD.status <> 'DANG_XU_LY' OR NEW.status <> 'XONG'
               OR ROW(NEW.key, NEW.user_id, NEW.path, NEW.created_at)
                  IS DISTINCT FROM ROW(OLD.key, OLD.user_id, OLD.path, OLD.created_at) THEN
                RAISE EXCEPTION 'form_submissions chỉ được chốt một lần';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER form_submissions_once BEFORE UPDATE ON form_submissions
        FOR EACH ROW EXECUTE FUNCTION form_submissions_guard();
    """)


def downgrade() -> None:
    op.execute('DROP TRIGGER form_submissions_once ON form_submissions')
    op.execute('DROP FUNCTION form_submissions_guard()')
    op.drop_index('ix_form_submissions_created_at', table_name='form_submissions')
    op.drop_table('form_submissions')
