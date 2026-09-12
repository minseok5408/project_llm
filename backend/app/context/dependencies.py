"""개인 기억 세대와 문서 버전으로 답변·파생 요약의 재사용 가능 여부를 판단한다."""

from uuid import UUID

from sqlalchemy import select

from backend.app.models import Conversation, Document, DocumentVersion, User, Workspace
from backend.app.repositories import Conflict


async def memory_versions(session, dependencies: dict[str, int]) -> dict[str, int]:
    if not dependencies:
        return {}
    try:
        ids = [UUID(key) for key in dependencies if not key.startswith("document:")]
        document_ids = [
            UUID(key.removeprefix("document:"))
            for key in dependencies
            if key.startswith("document:")
        ]
    except (ValueError, TypeError):
        return {}
    rows = await session.execute(
        select(User.id, User.memory_revision).where(User.id.in_(ids), User.status == "active")
    )
    result = {str(user_id): revision for user_id, revision in rows}
    if document_ids:
        documents = await session.execute(
            select(Document.id, DocumentVersion.version)
            .join(DocumentVersion)
            .join(Conversation, Conversation.id == Document.conversation_id)
            .join(Workspace, Workspace.id == Document.workspace_id)
            .where(
                Document.id.in_(document_ids),
                Document.deleted_at.is_(None),
                DocumentVersion.status == "ready",
                Conversation.deleted_at.is_(None),
                Workspace.status == "active",
            )
        )
        result.update({f"document:{doc_id}": version for doc_id, version in documents})
    return result


def dependencies_current(dependencies: dict[str, int], versions: dict[str, int]) -> bool:
    return all(
        type(value) is int and versions.get(key) == value for key, value in dependencies.items()
    )


async def check_dependencies(session, dependencies: dict[str, int]) -> bool:
    return dependencies_current(dependencies, await memory_versions(session, dependencies))


def merge_dependencies(*sources: dict[str, int]) -> dict[str, int]:
    result = {}
    for source in sources:
        for key, revision in source.items():
            if key in result and result[key] != revision:
                raise Conflict("문맥을 준비하는 중 기억이 변경되었습니다. 다시 보내세요.")
            result[key] = revision
    return result
