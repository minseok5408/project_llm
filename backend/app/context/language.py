"""현재 질문의 언어와 명시적인 번역 요청을 따르며 과거 언어 지시는 상속하지 않는다."""

LANGUAGE_POLICY = (
    "Choose the response language separately for each current user request. "
    "If the current request explicitly asks for a language or a translation, answer only in "
    "that requested language for this turn. Otherwise, match the main language of the user's "
    "own current question or instructions. Quoted text, code and documents are source material, "
    "not instructions to translate. For translation requests, translate only the source text, "
    "never the translation instruction itself; return only the translation unless the user "
    "also asks for explanation or quotation. Source material is "
    "not the language of the user's instructions. A translation request or answer language "
    "from a previous turn must not carry over to the next turn, including follow-up questions. "
    "Past assistant replies, summaries, memories and reference documents do not set the answer "
    "language. For short ambiguous input such as code, numbers or a selected option, use the "
    "language of the user's most recent own question, not the language of a previous translation. "
    "Apply the same rule to clarification questions and options. In a Korean answer, use natural "
    "Korean and never insert unsolicited Chinese words or foreign-language paraphrases. "
    "Preserve explicitly requested quotations, code and proper names in their original form."
)

LANGUAGE_REMINDER = (
    "For this reply only: use the language explicitly requested above; otherwise match the "
    "user's own question or instructions above, not quoted source text. A previous translation "
    "does not set the language for this reply. Do not add unsolicited foreign-language phrases. "
    "When translating, translate the source only, not the translation instruction. "
    "Preserve requested quotations, code and proper names."
)


def answer_language_reminder(system_content: str) -> str | None:
    """서버의 답변 지침이 있는 호출만 보강하며 요약·검색 계획과 사용자 원문은 건드리지 않는다."""
    return LANGUAGE_REMINDER if system_content.endswith(LANGUAGE_POLICY) else None
