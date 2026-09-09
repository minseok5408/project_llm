"""실제 계정이나 대화에 접근하지 않는 한국어 문맥 품질 평가 자료."""

from dataclasses import dataclass

from backend.app.context.builder import ContextTurn

FIXTURE_VERSION = 1


@dataclass(frozen=True, slots=True)
class ContextCase:
    """기대 어휘는 의미 정답 자체가 아니라 빠진 사실을 찾는 보조 지표다."""

    name: str
    turns: tuple[ContextTurn, ...]
    question: str
    expected: dict[str, tuple[str, ...]]
    source_output_limit: int | None = None


def _turn(
    index: int,
    user: str,
    assistant: str,
    status: str = "completed",
    finish_reason: str | None = "stop",
) -> ContextTurn:
    return ContextTurn(index * 2 + 1, index * 2 + 2, user, assistant, status, finish_reason)


def cases() -> list[ContextCase]:
    """고정된 가상 인물·수치로 실패를 비교하며 사용자의 저장 대화는 읽지 않는다."""
    long_turns = []
    milestones = {
        0: "내 이름은 이가온이야. 한국어로 답해줘.",
        2: "내 직업은 웹 개발자이고 Python을 사용해.",
        9: "이번 작업의 예산은 350만원이야.",
        47: "앞에서 말한 예산을 정정할게. 최종 예산은 420만원이야.",
        65: "배포 전에 backup_records 함수의 중복 실행 방지 테스트를 해야 해. 아직 미완료야.",
        80: "결정: 작업 마감은 10월 18일로 정했어.",
    }
    for index in range(108):
        user = milestones.get(index, f"임시 점검 {index}: 이 점검은 끝났고 새 결정은 없어.")
        long_turns.append(_turn(index, user, "확인했습니다. 다음 항목을 알려주세요."))
    return [
        ContextCase(
            "short_cancelled_recall",
            (
                _turn(0, "내 이름은 이가온이야. '알겠습니다'라고만 답해.", "알겠습니다."),
                _turn(
                    1,
                    "난 웹 개발자야. Python 웹 서버 만드는 법을 길게 설명해줘.",
                    "안",
                    "cancelled",
                    None,
                ),
            ),
            "내 이름과 직업이 뭐라고 했지? 이름과 직업을 한 문장으로 답해.",
            {"이름": ("이가온",), "직업": ("웹 개발자", "웹개발자")},
        ),
        ContextCase(
            "corrected_fact",
            (
                _turn(0, "내 직업은 디자이너야.", "디자이너시군요."),
                _turn(
                    1,
                    "정정할게. 디자이너는 이전 직업이고 지금은 데이터 분석가야.",
                    "알",
                    "cancelled",
                    None,
                ),
            ),
            "지금 내 직업만 말해줘.",
            {"현재 직업": ("데이터 분석가", "데이터분석가")},
        ),
        ContextCase(
            "numbers_and_decision",
            (
                _turn(0, "예산은 350만원, 일정은 10월 12일이야.", "기록했습니다."),
                _turn(
                    1,
                    "예산은 420만원으로 정정하고 날짜는 10월 18일로 결정했어.",
                    "변경을 확인했습니다.",
                ),
            ),
            "최종 예산과 날짜를 숫자로 답해줘.",
            {"예산": ("420", "4,200,000", "4200000"), "일정": ("10월 18일", "10/18", "10-18")},
        ),
        ContextCase(
            "unfinished_task",
            (
                _turn(
                    0,
                    "backup_records 함수의 중복 실행 방지 테스트는 아직 못 했어.",
                    "테스트 사례를",
                    "failed",
                    None,
                ),
                _turn(1, "문서 작성은 끝냈고 아까 말한 테스트는 여전히 미완료야.", "알겠습니다."),
            ),
            "남은 작업과 대상 함수 이름만 말해줘.",
            {"식별자": ("backup_records",), "작업": ("중복",)},
        ),
        ContextCase(
            "length_continuation",
            (
                _turn(
                    0,
                    "Python으로 인사 함수를 작성해줘.",
                    '```python\ndef greet(name):\n    message = f"안녕하세요, {name}님"\n    ',
                    finish_reason="length",
                ),
            ),
            "이어서 말해",
            {"코드 이어쓰기": ("return message", "print(message)")},
            source_output_limit=1024,
        ),
        ContextCase(
            "rolling_108_turns",
            tuple(long_turns),
            "내 이름, 직업, 사용하는 언어, 최종 예산과 마감일, "
            "미완료 작업의 함수 이름을 모두 알려줘.",
            {
                "이름": ("이가온",),
                "직업": ("개발자",),
                "언어": ("Python", "파이썬"),
                "예산": ("420", "4,200,000", "4200000"),
                "일정": ("10월 18일", "10/18", "10-18"),
                "미완료 함수": ("backup_records",),
            },
        ),
    ]
