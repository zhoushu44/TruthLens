"""Allow NULL documents.conversation_id (global knowledge base)

Revision ID: 003_documents_conv_nullable
Revises: 002_exact_quote, afcad0290ea1
Create Date: 2026-09-05

全局知识库（无会话）文档上传需要 conversation_id = NULL；
001 迁移将 documents.conversation_id 建成 NOT NULL，与 ORM nullable=True 不一致。
本迁移把该列改为可空（合并两条历史分支为单一 head）。

Revision ID: 003_documents_conv_nullable
Revises: 002_exact_quote, afcad0290ea1
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "003_documents_conv_nullable"
down_revision: Union[str, Sequence[str], None] = ("002_exact_quote", "afcad0290ea1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: allow conversation_id to be NULL for global documents."""
    op.alter_column(
        "documents",
        "conversation_id",
        existing_type=sa.String(length=36),
        nullable=True,
        existing_foreign_keys=sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
    )


def downgrade() -> None:
    """Downgrade schema: restore NOT NULL on documents.conversation_id."""
    # 若库内存在 NULL 行，此处会失败——降级前需先清理全局文档
    op.alter_column(
        "documents",
        "conversation_id",
        existing_type=sa.String(length=36),
        nullable=False,
        existing_foreign_keys=sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
    )
