"""add missing FK indexes

Revision ID: l6f7g8h9i0j1
Revises: k5f6g7h8i9j0
Create Date: 2026-04-28

Adds covering indexes on six foreign-key columns that were missing them:
  automation_chat.automation_task_id
  bullhorn_activity.monitor_id
  environment_alert.environment_status_id
  knowledge_document.source_ticket_id
  password_reset_token.user_id
  processing_log.schedule_config_id
"""
from alembic import op

revision = 'l6f7g8h9i0j1'
down_revision = 'k5f6g7h8i9j0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_automation_chat_automation_task_id',
                    'automation_chat', ['automation_task_id'], unique=False)
    op.create_index('ix_bullhorn_activity_monitor_id',
                    'bullhorn_activity', ['monitor_id'], unique=False)
    op.create_index('ix_environment_alert_environment_status_id',
                    'environment_alert', ['environment_status_id'], unique=False)
    op.create_index('ix_knowledge_document_source_ticket_id',
                    'knowledge_document', ['source_ticket_id'], unique=False)
    op.create_index('ix_password_reset_token_user_id',
                    'password_reset_token', ['user_id'], unique=False)
    op.create_index('ix_processing_log_schedule_config_id',
                    'processing_log', ['schedule_config_id'], unique=False)


def downgrade():
    op.drop_index('ix_processing_log_schedule_config_id', table_name='processing_log')
    op.drop_index('ix_password_reset_token_user_id', table_name='password_reset_token')
    op.drop_index('ix_knowledge_document_source_ticket_id', table_name='knowledge_document')
    op.drop_index('ix_environment_alert_environment_status_id', table_name='environment_alert')
    op.drop_index('ix_bullhorn_activity_monitor_id', table_name='bullhorn_activity')
    op.drop_index('ix_automation_chat_automation_task_id', table_name='automation_chat')
