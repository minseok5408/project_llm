"""대화 ACL을 먼저 적용한 유한한 후보에서 키워드와 로컬 벡터 순위를 결합한다."""

import math
from collections import Counter

from sqlalchemy import select

from backend.app.context.recall import recall_terms
from backend.app.files.policy import EMBEDDING_VERSION, MAX_CHUNKS, MAX_DOCUMENTS
from backend.app.models import Chunk, Document, DocumentVersion
from backend.app.repositories import Repository


async def candidates(session, actor_id, workspace_id, conversation_id):
    await Repository(session, actor_id).get_conversation(workspace_id, conversation_id)
    # 사용자 범위를 확인하기 전에 전역 벡터 검색이나 전역 후보 추출을 하지 않는다.
    return (
        await session.execute(
            select(Document, DocumentVersion, Chunk)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .join(Chunk, Chunk.version_id == DocumentVersion.id)
            .where(
                Document.workspace_id == workspace_id,
                Document.conversation_id == conversation_id,
                Document.deleted_at.is_(None),
                DocumentVersion.status == "ready",
                DocumentVersion.embedding_version == EMBEDDING_VERSION,
            )
            .order_by(Document.created_at, Document.id, Chunk.ordinal)
            .limit(MAX_DOCUMENTS * MAX_CHUNKS)
        )
    ).all()


def rank(question: str, rows, query_vector: list[float], *, method="hybrid", rerank=False):
    terms = recall_terms(question)
    texts = [(d.filename + " " + c.content).casefold() for d, _, c in rows]
    frequencies = Counter({term: sum(term in value for value in texts) for term in terms})
    average = sum(len(value) for value in texts) / max(1, len(texts))
    keyword, vector = [], []
    for index, (_, _, chunk) in enumerate(rows):
        value = texts[index]
        score = 0.0
        for term in terms:
            frequency = value.count(term)
            inverse = math.log(
                1 + (len(texts) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
            )
            score += (
                inverse * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * len(value) / average))
            )
        keyword.append(score)
        vector.append(sum(a * b for a, b in zip(query_vector, chunk.embedding, strict=True)))
    ranks = []
    for scores in (keyword, vector):
        ranks.append(
            {
                index: place
                for place, index in enumerate(
                    sorted(range(len(rows)), key=lambda index: (-scores[index], index)), 1
                )
            }
        )
    scores = [
        (
            keyword[i]
            if method == "keyword"
            else vector[i]
            if method == "vector"
            else (1 / (30 + ranks[0][i]) if keyword[i] else 0) + 1 / (30 + ranks[1][i])
        )
        for i in range(len(rows))
    ]
    ordered = sorted(range(len(rows)), key=lambda i: (-scores[i], i))
    if rerank:
        # 추가 모델 없이 정확한 용어 일치를 가산하는 후보 재정렬을 평가용으로 비교한다.
        shortlist = ordered[:12]
        ordered = sorted(shortlist, key=lambda i: (-(scores[i] + 0.002 * keyword[i]), i))
    return [i for i in ordered if keyword[i] > 0 or vector[i] >= 0.7]


async def retrieve(rows, embeddings, question: str) -> list[dict]:
    if not rows:
        return []
    vectors = await embeddings.encode([question[:2000]], query=True)
    selected, seen = [], []
    for index in rank(question, rows, vectors[0], rerank=True):
        document, version, chunk = rows[index]
        # 겹침 조각만으로 결과를 채우지 않아 한 답변의 근거 범위를 넓힌다.
        if any(
            doc_id == document.id
            and page == chunk.page
            and min(end, chunk.end_char) - max(start, chunk.start_char) > len(chunk.content) // 2
            for doc_id, page, start, end in seen
        ):
            continue
        seen.append((document.id, chunk.page, chunk.start_char, chunk.end_char))
        selected.append(
            {
                "source": {
                    "document_id": str(document.id),
                    "chunk_id": str(chunk.id),
                    "filename": document.filename,
                    "page": chunk.page,
                    "chunk": chunk.ordinal,
                    "start": chunk.start_char,
                    "end": chunk.end_char,
                },
                "content": chunk.content,
                "dependencies": {f"document:{document.id}": version.version},
            }
        )
        if len(selected) == 4:
            break
    return selected
