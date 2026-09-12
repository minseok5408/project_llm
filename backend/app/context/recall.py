"""권한 있는 현재 대화의 압축 이전 원문에서 짧은 근거 구절을 회수한다."""

import re
from dataclasses import dataclass

from sqlalchemy import case, or_, select, text

from backend.app.context.builder import CONTEXT_STATUSES
from backend.app.context.dependencies import dependencies_current, memory_versions
from backend.app.models import GenerationRun, Message

STOP_WORDS = frozenset(
    "이전 아까 전에 그때 대화 내용 원문 다시 알려줘 알려주세요 뭐였지 무엇 어떻게 했던 했었지 "
    "값 값을 값이 다음 질문 기억 기억해 말한 말했던 정한 정했던 찾아줘 "
    "확인해줘 부탁해 그리고 그거 그것 이거 이것 "
    "the what was were did earlier before please recall remember about tell me that this".split()
)
PARTICLES = (
    "에서는",
    "으로",
    "에서",
    "했던",
    "이라고",
    "라는",
    "이랑",
    "하고",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "도",
    "와",
    "과",
)


def needs_recall(question: str) -> bool:
    return bool(
        re.search(
            r"아까|이전|그때|원문|처음|앞서|정한|정했던|결정|합의|기억|뭐였|무엇이었|"
            r"earlier|previous|before|agreed|decided|remember|recall|[a-zA-Z]\w*_\w+",
            question[:2000],
            re.IGNORECASE,
        )
    )


def recall_terms(question: str) -> list[str]:
    terms = []
    for word in re.findall(r"[\w.-]+", question[:2000], re.UNICODE):
        word = word.casefold().strip("._-")[:80]
        if word in STOP_WORDS:
            continue
        if re.search(r"[가-힣]$", word):
            for ending in PARTICLES:
                if word.endswith(ending) and len(word) - len(ending) >= 2:
                    word = word[: -len(ending)]
                    break
        if len(word) >= 2 and word not in STOP_WORDS and word not in terms:
            terms.append(word)
        if len(terms) == 8:
            break
    return terms


@dataclass(frozen=True)
class RecallHit:
    source: dict
    content: str
    dependencies: dict[str, int]


def excerpt(content: str, terms: list[str], limit: int) -> tuple[str, int, int]:
    matches = [
        match for term in terms if (match := re.search(re.escape(term), content, re.IGNORECASE))
    ]
    start = max(0, min((match.start() for match in matches), default=0) - limit // 4)
    end = min(len(content), start + limit)
    return content[start:end], start, end


async def recall_originals(
    session, conversation, question: str, through: int, *, limit: int = 3, max_chars: int = 3600
) -> list[RecallHit]:
    terms = recall_terms(question)
    if through < 1 or not terms or not needs_recall(question):
        return []
    predicates = [Message.content.icontains(term, autoescape=True) for term in terms]
    score = sum(case((predicate, 1), else_=0) for predicate in predicates)
    # 현재 대화 인덱스로 범위를 줄이고 큰 이력에서도 검색 시간을 제한한다.
    await session.execute(text("SET LOCAL statement_timeout = '1500ms'"))
    rows = (
        await session.execute(
            select(Message, GenerationRun.memory_dependencies)
            .join(
                GenerationRun,
                or_(
                    (Message.id == GenerationRun.user_message_id)
                    & (Message.role == "user")
                    & (Message.created_by == GenerationRun.user_id),
                    (Message.id == GenerationRun.assistant_message_id)
                    & (Message.role == "assistant"),
                ),
            )
            .where(
                Message.workspace_id == conversation.workspace_id,
                Message.conversation_id == conversation.id,
                Message.sequence <= through,
                GenerationRun.workspace_id == conversation.workspace_id,
                GenerationRun.conversation_id == conversation.id,
                GenerationRun.is_current.is_(True),
                GenerationRun.status.in_(CONTEXT_STATUSES),
                or_(*predicates),
            )
            .order_by(score.desc(), Message.sequence.desc())
            .limit(24)
        )
    ).all()
    dependencies = {key: value for _, deps in rows for key, value in deps.items()}
    versions = await memory_versions(session, dependencies)
    result = []
    for message, deps in rows:
        if message.role == "assistant" and not dependencies_current(deps, versions):
            continue
        value, start, end = excerpt(message.content, terms, max_chars // limit)
        result.append(
            RecallHit(
                source={
                    "message_id": str(message.id),
                    "sequence": message.sequence,
                    "role": message.role,
                    "status": message.status,
                    "start": start,
                    "end": end,
                },
                content=value,
                dependencies=dict(deps) if message.role == "assistant" else {},
            )
        )
        if len(result) == limit:
            break
    return sorted(result, key=lambda hit: hit.source["sequence"])
