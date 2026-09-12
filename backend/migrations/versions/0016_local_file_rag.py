"""로컬 파일 수집과 대화 권한 범위 검색 메타데이터를 추가한다."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0016_local_file_rag"
down_revision = "0015_question_progress"
branch_labels = None
depends_on = None


def identity():
    return [
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade():
    op.create_table(
        "documents",
        *identity(),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column(
            "uploaded_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "conversation_id", "id"),
    )
    op.create_index("ix_documents_conversation", "documents", ["conversation_id", "deleted_at"])
    op.create_table(
        "document_versions",
        *identity(),
        sa.Column(
            "document_id",
            sa.UUID(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(80), nullable=False),
        sa.Column("extension", sa.String(10), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="uploaded"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(60)),
        sa.Column("parser_version", sa.String(40)),
        sa.Column("embedding_version", sa.String(160)),
        sa.Column("scan_engine", sa.String(40)),
        sa.Column("page_count", sa.Integer()),
        sa.Column("chunk_count", sa.Integer()),
        sa.UniqueConstraint("document_id", "version"),
        sa.CheckConstraint("version > 0 AND byte_size > 0", name="sizes"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256"),
        sa.CheckConstraint(
            "status IN ('uploaded','scanning','parsing','indexing','ready','failed')", name="status"
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 3", name="attempts"),
    )
    op.create_index("ix_document_versions_queue", "document_versions", ["status", "created_at"])
    op.create_table(
        "chunks",
        *identity(),
        sa.Column(
            "version_id",
            sa.UUID(),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", pg.ARRAY(sa.REAL()), nullable=False),
        sa.UniqueConstraint("version_id", "ordinal"),
        sa.CheckConstraint("ordinal > 0 AND page > 0 AND start_char >= 0", name="location"),
        sa.CheckConstraint(
            "end_char > start_char AND char_length(content) <= 1600", name="content"
        ),
        sa.CheckConstraint("cardinality(embedding) = 384", name="embedding_dimension"),
    )
    op.create_table(
        "attachments",
        *identity(),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id", "document_id"],
            ["documents.workspace_id", "documents.conversation_id", "documents.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("document_id"),
    )
    op.add_column(
        "generation_runs",
        sa.Column(
            "file_sources", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )
    op.create_check_constraint(
        op.f("ck_generation_runs_file_sources"),
        "generation_runs",
        "jsonb_typeof(file_sources) = 'array'",
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM documents)")):
        raise RuntimeError("파일 이력이 있어 자동 롤백할 수 없습니다. 백업을 확인하세요.")
    op.drop_constraint(op.f("ck_generation_runs_file_sources"), "generation_runs", type_="check")
    op.drop_column("generation_runs", "file_sources")
    for table in ("attachments", "chunks", "document_versions", "documents"):
        op.drop_table(table)
