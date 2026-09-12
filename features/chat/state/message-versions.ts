import type { ChatMessage } from './conversation-types.ts';

export type MessageVersionGroup = {
  id: string;
  messages: ChatMessage[];
};
export type MessageReveal = { messageId: string; key: string | number };
export type VersionSelection = {
  messageId: string | null;
  activeMessageId: string | null;
  revealKey: string | number | null;
};

function canJoin(previous: ChatMessage, next: ChatMessage): boolean {
  if (
    previous.role !== 'assistant' ||
    next.role !== 'assistant' ||
    previous.conversation_id !== next.conversation_id ||
    previous.sequence + 1 !== next.sequence ||
    !previous.generation_id ||
    !next.generation_id ||
    previous.is_current !== false ||
    typeof next.is_current !== 'boolean'
  )
    return false;
  // 예전 응답의 메타데이터가 없는 경우에만 연속 순번과 이전 버전 표시로 묶는다.
  return (
    !previous.user_message_id ||
    !next.user_message_id ||
    previous.user_message_id === next.user_message_id
  );
}

/** 원본 순서를 바꾸지 않고 같은 질문의 인접한 생성 버전만 하나의 표시 단위로 묶는다. */
export function groupMessageVersions(
  messages: readonly ChatMessage[],
): MessageVersionGroup[] {
  const groups: MessageVersionGroup[] = [];
  for (const message of messages) {
    const group = groups.at(-1);
    const previous = group?.messages.at(-1);
    const questionId = group?.messages.find(
      (candidate) => candidate.user_message_id,
    )?.user_message_id;
    if (
      group &&
      previous &&
      canJoin(previous, message) &&
      (!questionId ||
        !message.user_message_id ||
        questionId === message.user_message_id)
    )
      group.messages.push(message);
    else groups.push({ id: message.id, messages: [message] });
  }
  const occurrences = new Map<string, number>();
  for (const group of groups) {
    const question = group.messages.find(
      (message) => message.role === 'assistant' && message.user_message_id,
    );
    if (!question) continue;
    // 앞 페이지의 더 오래된 버전이 붙어도 현재 선택한 버전의 컴포넌트를 유지한다.
    const key = `${question.conversation_id}:question:${question.user_message_id}`;
    const occurrence = (occurrences.get(key) ?? 0) + 1;
    occurrences.set(key, occurrence);
    group.id = `${key}:${occurrence}`;
  }
  return groups;
}

/** 새 생성·검색 선택은 반영하되 토큰 갱신마다 사용자의 버전 탐색을 되돌리지 않는다. */
export function resolveVersionSelection(
  group: MessageVersionGroup,
  selection: VersionSelection,
  activeMessageId?: string | null,
  reveal?: MessageReveal | null,
): VersionSelection {
  const includes = (id?: string | null) =>
    Boolean(id && group.messages.some((message) => message.id === id));
  const active = includes(activeMessageId) ? activeMessageId! : null;
  const requested = reveal && includes(reveal.messageId) ? reveal : null;
  const latest =
    group.messages.findLast((message) => message.is_current !== false) ??
    group.messages.at(-1);
  const messageId =
    requested && requested.key !== selection.revealKey
      ? requested.messageId
      : active && active !== selection.activeMessageId
        ? active
        : includes(selection.messageId)
          ? selection.messageId
          : (latest?.id ?? null);
  const revealKey = requested?.key ?? null;
  return messageId === selection.messageId &&
    active === selection.activeMessageId &&
    revealKey === selection.revealKey
    ? selection
    : { messageId, activeMessageId: active, revealKey };
}
