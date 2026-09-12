import assert from 'node:assert/strict';
import { register } from 'node:module';
import { describe, it } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  groupMessageVersions,
  resolveVersionSelection,
} from '../features/chat/state/message-versions.ts';

register('./helpers/tsx-loader.mjs', import.meta.url);
const { MessageVersionGroup } =
  await import('../features/chat/components/message-version-group.tsx');
const { ChatMessageView } =
  await import('../features/chat/components/chat-message-view.tsx');
const { ChatComposer } =
  await import('../features/chat/components/chat-composer.tsx');
const { UserMessageText } =
  await import('../features/chat/components/user-message-text.tsx');

const message = (id, sequence, patch = {}) => ({
  id,
  sequence,
  conversation_id: 'conversation',
  role: 'assistant',
  content: `원문 ${id}`,
  status: 'completed',
  token_count: 20,
  created_at: '2026-09-12T00:00:00Z',
  generation_id: `generation-${id}`,
  user_message_id: 'question',
  is_current: true,
  ...patch,
});
const before = message('before', 2, { is_current: false });
const current = message('current', 3);
const [group] = groupMessageVersions([before, current]);
const emptySelection = {
  messageId: null,
  activeMessageId: null,
  revealKey: null,
};
const baseView = {
  userName: '사용자',
  streaming: false,
  cancelling: false,
  compacting: false,
  reducedMotion: false,
  lengthLimited: false,
  canSend: true,
  onContinue() {},
  onRegenerate() {},
};
const renderGroup = (props = {}) =>
  renderToStaticMarkup(
    createElement(
      MessageVersionGroup,
      { group, registerMessage() {}, ...props },
      (selected) =>
        createElement(ChatMessageView, { ...baseView, message: selected }),
    ),
  );

describe('질문별 답변 버전 표시', () => {
  it('같은 질문의 인접 버전과 질문 카드를 묶고 원문 객체·순서·다른 질문은 유지한다', () => {
    const question = message('question', 1, { role: 'user' });
    const card = message('card', 4, {
      question_card: {
        questions: [{ question: '원하는 형식은?', options: [] }],
      },
    });
    const secondQuestion = message('second-question', 5, { role: 'user' });
    const answer = message('second-answer', 6, {
      user_message_id: 'second-question',
    });
    const records = Object.freeze(
      [
        question,
        before,
        { ...current, is_current: false },
        card,
        secondQuestion,
        answer,
      ].map(Object.freeze),
    );
    const groups = groupMessageVersions(records);
    assert.deepEqual(
      groups.map((item) => item.messages.map((record) => record.id)),
      [
        ['question'],
        ['before', 'current', 'card'],
        ['second-question'],
        ['second-answer'],
      ],
    );
    assert.equal(groups[1].messages[2], card);
    assert.deepEqual(
      groups.flatMap((item) => item.messages),
      records,
    );
  });

  it('다른 질문·대화·역할·순번 공백이나 확인되지 않은 생성 메시지는 섞지 않는다', () => {
    for (const patch of [
      { user_message_id: 'another-question' },
      { conversation_id: 'another-conversation' },
      { role: 'system' },
      { sequence: 4 },
      { generation_id: undefined },
      { is_current: undefined },
    ])
      assert.equal(
        groupMessageVersions([before, { ...current, ...patch }]).length,
        2,
      );
    assert.equal(
      groupMessageVersions([{ ...before, is_current: true }, current]).length,
      2,
    );
  });

  it('구형 메타데이터는 인접 이전 버전으로만 묶고 누락값으로 다른 질문을 연결하지 않는다', () => {
    const missing = message('missing', 3, {
      user_message_id: undefined,
      is_current: false,
    });
    const another = message('another', 4, {
      user_message_id: 'different-question',
    });
    assert.deepEqual(
      groupMessageVersions([before, missing, another]).map(
        (item) => item.messages.length,
      ),
      [2, 1],
    );
    assert.equal(
      groupMessageVersions([{ ...before, user_message_id: undefined }, current])
        .length,
      1,
    );
  });

  it('페이지 중간부터 받은 버전도 현재 답변을 선택하고 앞 페이지가 붙어도 원문은 보존한다', () => {
    const state = resolveVersionSelection(group, emptySelection);
    assert.equal(state.messageId, current.id);
    const older = message('older', 1, { is_current: false });
    const [expanded] = groupMessageVersions([older, before, current]);
    assert.equal(expanded.id, group.id);
    assert.equal(resolveVersionSelection(expanded, state), state);
  });

  it('새 스트림은 최신 버전으로 이동하지만 델타와 완료는 수동 탐색을 덮지 않는다', () => {
    const newMessage = message('new', 4, { status: 'pending' });
    const [updated] = groupMessageVersions([
      before,
      { ...current, is_current: false },
      newMessage,
    ]);
    const initial = resolveVersionSelection(group, emptySelection);
    const started = resolveVersionSelection(updated, initial, 'new');
    assert.equal(started.messageId, 'new');
    const browsing = { ...started, messageId: 'before' };
    assert.equal(resolveVersionSelection(updated, browsing, 'new'), browsing);
    const finished = resolveVersionSelection(updated, browsing, null);
    assert.equal(finished.messageId, 'before');
    const next = message('next', 5, { status: 'pending' });
    const [again] = groupMessageVersions([
      before,
      { ...current, is_current: false },
      { ...newMessage, is_current: false },
      next,
    ]);
    assert.equal(
      resolveVersionSelection(again, finished, 'next').messageId,
      'next',
    );
  });

  it('검색 요청은 이전 버전을 열고 같은 결과 재선택도 처리하며 다른 그룹에는 영향이 없다', () => {
    const initial = resolveVersionSelection(group, emptySelection);
    const reveal = { messageId: 'before', key: 1 };
    const found = resolveVersionSelection(group, initial, null, reveal);
    assert.equal(found.messageId, 'before');
    const manual = { ...found, messageId: 'current' };
    assert.equal(resolveVersionSelection(group, manual, null, reveal), manual);
    assert.equal(
      resolveVersionSelection(group, manual, null, { ...reveal, key: 2 })
        .messageId,
      'before',
    );
    assert.equal(
      resolveVersionSelection(group, initial, null, {
        messageId: 'another',
        key: 3,
      }),
      initial,
    );
  });

  it('한 표시 영역에서 페이지 번호와 선택된 원문만 표시하며 이전 답변도 검색으로 열린다', () => {
    const latest = renderGroup();
    assert.match(latest, /2 \/ 2/);
    assert.match(latest, /원문 current/);
    assert.doesNotMatch(latest, /원문 before|<details/);
    assert.equal((latest.match(/<article/g) ?? []).length, 1);
    const previous = renderGroup({
      reveal: { messageId: 'before', key: 'search-1' },
    });
    assert.match(previous, /1 \/ 2/);
    assert.match(previous, /원문 before/);
    assert.doesNotMatch(previous, /원문 current|aria-label="다시 생성"/);
    assert.match(previous, /aria-label="이전 답변 버전" disabled=""/);
  });
});

describe('긴 질문 편집과 원문 펼치기', () => {
  it('긴 원문은 자르지 않고 접으며 검색으로 선택한 원문은 처음부터 펼친다', () => {
    const content = '긴 질문입니다.\n'.repeat(20) + '원문의 마지막 줄';
    const compact = renderToStaticMarkup(
      createElement(UserMessageText, { content }),
    );
    assert.match(compact, /line-clamp-8/);
    assert.match(compact, /aria-expanded="false"/);
    assert.match(compact, /원문의 마지막 줄/);
    assert.match(compact, /더 보기/);
    const revealed = renderToStaticMarkup(
      createElement(UserMessageText, { content, revealKey: 1 }),
    );
    assert.match(revealed, /aria-expanded="true"/);
    assert.doesNotMatch(revealed, /line-clamp-8/);
    assert.match(revealed, /원문의 마지막 줄/);
    const short = renderToStaticMarkup(
      createElement(UserMessageText, { content: '짧은 질문' }),
    );
    assert.equal(short, '짧은 질문');
  });

  const composerProps = {
    state: {
      selected: { id: 'conversation' },
      draft: '편집할 긴 질문\n'.repeat(30),
      sending: false,
      stream: 'live',
      generation: { status: 'running' },
    },
    userName: '사용자',
    isGenerating: true,
    cancelling: false,
    compacting: false,
    searching: false,
    noBalance: false,
    logoutPending: false,
    canSend: false,
    thinking: false,
    onReconnect() {},
    onCancel() {},
    onSend() {},
    onDraftChange() {},
    onThinkingChange() {},
  };
  it('확대 조작은 같은 입력창을 가리키며 생성 중 초안 편집과 중단 버튼을 유지한다', () => {
    const html = renderToStaticMarkup(
      createElement(ChatComposer, composerProps),
    );
    assert.match(html, /aria-label="입력창 확대"/);
    assert.match(html, /aria-controls="chat-message-input"/);
    assert.match(html, /aria-expanded="false"/);
    assert.match(html, /aria-label="답변 중단"/);
    assert.doesNotMatch(html, /<textarea[^>]*disabled=""/);
    assert.equal((html.match(/<textarea/g) ?? []).length, 1);
    assert.ok(html.includes(composerProps.state.draft));
  });

  it('입력창을 감싸도 한글 조합·줄바꿈은 보존하고 전송 가능한 Enter만 제출한다', () => {
    const findInput = (node) => {
      if (node?.props?.id === 'chat-message-input') return node;
      for (const child of [node?.props?.children].flat()) {
        const found =
          child && typeof child === 'object' ? findInput(child) : null;
        if (found) return found;
      }
      return null;
    };
    let sent = 0;
    for (const [canSend, shiftKey, isComposing, expected] of [
      [false, false, false, 0],
      [true, false, true, 0],
      [true, true, false, 0],
      [true, false, false, 1],
    ]) {
      let prevented = false;
      const input = findInput(
        ChatComposer({ ...composerProps, canSend, onSend: () => sent++ }),
      );
      input.props.onKeyDown({
        key: 'Enter',
        shiftKey,
        nativeEvent: { isComposing },
        preventDefault() {
          prevented = true;
        },
      });
      assert.equal(sent, expected);
      assert.equal(prevented, Boolean(expected));
    }
  });
});
