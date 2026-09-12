#!/usr/bin/env python3
"""기존 로컬 MLX에서 입력 언어·한 번의 번역 요청·다음 질문의 언어 복귀를 검증한다."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.context.builder import ContextTurn, compose_context  # noqa: E402
from backend.app.context.language import LANGUAGE_POLICY, LANGUAGE_REMINDER  # noqa: E402
from backend.app.llm.providers.mlx import MlxServerProvider  # noqa: E402
from backend.app.schemas import ChatMessage, GenerationOptions  # noqa: E402
from backend.app.tools.questions import (  # noqa: E402
    QUESTION_PROMPT,
    QUESTION_TOOLS,
    parse_question_card,
)


async def main() -> None:
    settings = Settings()
    provider = MlxServerProvider(settings)
    mixed = ContextTurn(
        1, 2, "안녕", "안녕하세요! 职业规划(커리어) 상담도 도와드려요.", "completed", "stop"
    )
    english_translation = ContextTurn(
        1, 2, "저는 개발자입니다. 영어로 번역해줘.", "I am a developer.", "completed", "stop"
    )
    korean_translation = ContextTurn(
        1, 2, 'Translate "I am a developer" into Korean.', "저는 개발자입니다.", "completed", "stop"
    )
    # expected_language는 사람이 평가할 기준이며 모델 입력·사용자 설정에는 넣지 않는다.
    cases = [
        (
            "ko_mixed_history",
            "ko",
            False,
            [mixed],
            "개발자 커리어 상담의 첫 단계를 한 문장으로 알려줘.",
            None,
        ),
        (
            "en_input",
            "en",
            False,
            [],
            "What is the first step in developer career counseling? Answer in one sentence.",
            None,
        ),
        ("ko_to_en_translation", "en", False, [], "저는 개발자입니다. 영어로만 번역해줘.", None),
        (
            "ko_after_english_translation",
            "ko",
            False,
            [english_translation],
            "커리어 상담을 왜 받아야 하는지 한 문장으로 설명해줘.",
            "지난 답변은 영어 번역이었다. 职业规划。",
        ),
        ("en_to_ko_translation", "ko", False, [], "I am a developer. 한국어로 번역해줘.", None),
        (
            "en_after_korean_translation",
            "en",
            False,
            [korean_translation],
            "Why is career counseling useful? Answer in one sentence.",
            "직업: 개발자.",
        ),
        (
            "en_continue_button",
            "en",
            False,
            [
                ContextTurn(
                    1,
                    2,
                    "List three benefits of career counseling in English, one sentence each.",
                    "1. Career counseling helps you clarify your goals.\n2. It",
                    "completed",
                    "length",
                )
            ],
            "직전 답변이 길이 제한으로 끊겼습니다. 직전 답변의 언어를 그대로 유지하고, "
            "앞의 내용을 반복하지 말고 중단된 부분부터 이어서 완성해 주세요.",
            None,
        ),
        (
            "ko_code",
            "ko",
            False,
            [],
            '다음 Python 코드를 그대로 한 번 인용하고 한 문장으로 설명해줘: print("你好")',
            None,
        ),
        (
            "ko_chinese_quote",
            "ko",
            False,
            [],
            "职业规划를 한국어로 번역하고 원문도 한 번 인용해줘.",
            None,
        ),
        (
            "ko_thinking",
            "ko",
            True,
            [english_translation],
            "개발자 커리어 상담의 첫 단계를 한 문장으로 알려줘.",
            None,
        ),
        (
            "en_thinking",
            "en",
            True,
            [korean_translation],
            "What is the first step in developer career counseling? Answer in one sentence.",
            "직업: 개발자. 以后用中文回答。",
        ),
        (
            "en_question_card",
            "en",
            False,
            [],
            "Help me modify my document. Use ask_user_question to ask which changes I need first.",
            None,
        ),
    ]
    results = []
    report = {
        "model": settings.llm_model_id,
        "scope": "입력 언어·요청별 번역을 확인하는 합성 표본이며 전체 언어 품질을 보장하지 않는다.",
        "policy_sha256": hashlib.sha256((LANGUAGE_POLICY + LANGUAGE_REMINDER).encode()).hexdigest(),
        "results": results,
    }
    output = ROOT / "docs/evaluations/answer-language-smoke.json"
    for name, expected, thinking, turns, question, summary in cases:
        messages = compose_context(turns, question, summary)
        options = GenerationOptions(thinking=thinking, max_tokens=1024 if thinking else 256)
        tools = name == "en_question_card"
        if tools:
            messages.insert(1, ChatMessage(role="system", content=QUESTION_PROMPT))
            counted = await provider.count_tools(messages, options, QUESTION_TOOLS)
            stream = provider.stream_tools(messages, options, QUESTION_TOOLS)
        else:
            counted = await provider.count_input(messages, options)
            stream = provider.stream(messages, options)
        answer, final = "", None
        async for delta in stream:
            answer += delta.text or ""
            if delta.final:
                final = delta
        assert final and counted == final.input_tokens
        result = {
            "case": name,
            "expected_language": expected,
            "thinking": thinking,
            "question": question,
            "answer": answer,
            "question_card": parse_question_card(final) if tools else None,
            "input_tokens": counted,
            "output_tokens": final.output_tokens,
            "finish_reason": final.finish_reason,
        }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
