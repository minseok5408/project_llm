import assert from 'node:assert/strict';
import { register } from 'node:module';
import { it } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  ConversationSession,
  INITIAL_STATE,
} from '../features/chat/state/conversation-session.ts';
import { reduceGenerationEvent } from '../features/chat/state/generation-events.ts';
import { validProgress } from '../features/chat/state/interaction-types.ts';

register('./helpers/tsx-loader.mjs', import.meta.url);
const { QuestionCard } =
  await import('../features/chat/components/question-card.tsx');
const { ChatMessageView } =
  await import('../features/chat/components/chat-message-view.tsx');
const card = {
  questions: [
    { question: '어떤 결과물이 필요한가요?', options: ['문서', '코드'] },
    { question: '조건을 알려주세요.', options: [] },
  ],
};
const message = {
  id: 'a',
  role: 'assistant',
  conversation_id: 'c',
  generation_id: 'g',
  question_card: card,
  can_respond: true,
  content: '확인 질문',
  status: 'completed',
  sequence: 2,
};
const conversation = {
  id: 'c',
  workspace_id: 'w',
  title: '진행 중인 대화',
  status: 'active',
};
const renderCard = (props = {}) =>
  renderToStaticMarkup(
    createElement(QuestionCard, {
      card,
      answers: [],
      available: true,
      canSend: true,
      onChange() {},
      onSubmit() {},
      ...props,
    }),
  );

it('질문 카드에 선택지와 직접 입력을 함께 제공하고 미응답 전송을 차단한다', () => {
  const html = renderCard();
  assert.match(html, /<form[^>]*aria-label="확인 질문"/);
  assert.match(html, /aria-pressed="false"/);
  assert.equal((html.match(/<textarea/g) ?? []).length, 2);
  assert.match(html, /type="submit" disabled=""/);
  const answered = renderCard({ answers: ['코드', '조건'] });
  assert.match(answered, /aria-pressed="true"/);
  assert.doesNotMatch(answered, /type="submit" disabled/);
  const busy = renderCard({ answers: ['코드', '조건'], canSend: false });
  assert.match(busy, /type="submit" disabled=""/);
  assert.doesNotMatch(busy, /<textarea[^>]*disabled/);
});

it('응답한 카드와 지난 카드는 다시 보낼 수 없고 입력값은 HTML로 해석하지 않는다', () => {
  const saved = renderCard({
    card: {
      ...card,
      answers: ['<script>test</script>', '조건'],
      response_generation_id: 'next',
    },
    available: false,
  });
  assert.match(saved, /답변한 확인 질문/);
  assert.match(saved, /&lt;script&gt;/);
  assert.doesNotMatch(saved, /<textarea|type="submit"|<script>/);
  const stale = renderCard({ available: false });
  assert.match(stale, /지난 확인 질문/);
  assert.doesNotMatch(stale, /<textarea|type="submit"/);
});

it('답변 생성 중과 완료 후 처리 과정을 표시하지 않고 본문과 질문 카드를 유지한다', () => {
  const progress = [
    { id: 'context', name: 'context', status: 'completed' },
    { id: 'search', name: 'web_search', status: 'failed' },
    { id: 'answer', name: 'answer', status: 'skipped' },
  ];
  for (const streaming of [true, false]) {
    for (const questionCard of [undefined, card]) {
      const html = renderToStaticMarkup(
        createElement(ChatMessageView, {
          message: {
            ...message,
            content: '보존된 답변 본문',
            question_card: questionCard,
            progress,
          },
          userName: '김민석',
          streaming,
          cancelling: false,
          compacting: false,
          reducedMotion: true,
          lengthLimited: false,
          canSend: !streaming,
          onRegenerate() {},
          onContinue() {},
        }),
      );
      assert.doesNotMatch(
        html,
        /처리 과정|진행 계획|응답 진행 단계|문맥과 참고 자료 준비|웹 자료 검색|1\/3 완료/,
      );
      if (questionCard) {
        assert.match(html, /<form[^>]*aria-label="확인 질문"/);
        assert.match(html, /어떤 결과물이 필요한가요/);
      } else if (streaming) {
        assert.match(html, /답변 생성 중/);
        assert.match(html, /응답을 준비하는 중/);
      } else {
        assert.match(html, /보존된 답변 본문/);
      }
    }
  }
});

it('화면에 표시하지 않는 내부 진행 상태의 형식을 검증한다', () => {
  const items = [
    { id: 'context', name: 'context', status: 'completed' },
    { id: 'search', name: 'web_search', status: 'failed' },
    { id: 'answer', name: 'answer', status: 'skipped' },
  ];
  assert.ok(validProgress(items));
  assert.ok(!validProgress([...items, items[0]]));
  assert.ok(!validProgress([{ id: 'x', name: 'x', status: 'approved' }]));
});

it('질문 일부만 입력하면 전송하지 않고 실패 재시도는 같은 요청 키와 입력을 유지한다', async () => {
  const attempts = [];
  const store = new ConversationSession(
    async (url, init) => {
      attempts.push({ url, ...init });
      throw new Error('연결 실패');
    },
    () => {},
    () => ({ network_mode: 'local', web_search: 'off' }),
  );
  store.seed({
    workspaceId: 'w',
    selected: conversation,
    messages: [message],
    loading: false,
  });
  store.setDraft('다음 질문 초안');
  store.setQuestionAnswer('a', 1, '둘째 답');
  assert.equal(await store.respondToQuestions('a', { thinking: false }), false);
  assert.equal(attempts.length, 0);
  store.setQuestionAnswer('a', 0, '코드');
  await store.respondToQuestions('a', { thinking: false });
  await store.respondToQuestions('a', { thinking: false });
  assert.equal(attempts.length, 2);
  assert.equal(attempts[0].url, '/api/v1/generations/g/respond');
  assert.equal(
    attempts[0].headers['Idempotency-Key'],
    attempts[1].headers['Idempotency-Key'],
  );
  assert.deepEqual(JSON.parse(attempts[0].body), {
    answers: ['코드', '둘째 답'],
    options: { thinking: false },
    network_mode: 'local',
    web_search: 'off',
  });
  assert.equal(store.getSnapshot().draft, '다음 질문 초안');
  assert.deepEqual(store.getSnapshot().questionDrafts.a, ['코드', '둘째 답']);
  store.dispose();
});

it('생성 중·보관·지난 질문 카드는 새 응답을 시작하지 않는다', async () => {
  const store = new ConversationSession(
    async () => assert.fail('전송하면 안 됨'),
    () => {},
  );
  store.seed({
    workspaceId: 'w',
    selected: conversation,
    messages: [message],
    loading: false,
  });
  store.setQuestionAnswer('a', 0, '코드');
  store.setQuestionAnswer('a', 1, '조건');
  for (const patch of [
    { generation: { id: 'running' } },
    { generation: null, selected: { ...conversation, status: 'archived' } },
    { selected: conversation, messages: [{ ...message, can_respond: false }] },
  ]) {
    store.seed(patch);
    assert.equal(
      await store.respondToQuestions('a', { thinking: false }),
      false,
    );
  }
  store.dispose();
});

it('종료 이벤트의 진행 상태를 메시지에 보존하고 다른 생성 이벤트를 무시한다', () => {
  const state = {
    ...INITIAL_STATE,
    messages: [message],
    generation: { id: 'g', assistant_message_id: 'a', status: 'running' },
  };
  const before = structuredClone(state);
  const cursor = { lastEventId: 0, replayText: '' };
  const progress = [{ id: 'answer', name: 'answer', status: 'cancelled' }];
  const ignored = reduceGenerationEvent(state, cursor, {
    id: 1,
    event: 'meta',
    data: { generation_id: 'other', progress },
  });
  assert.equal(ignored, null);
  const applied = reduceGenerationEvent(state, cursor, {
    id: 2,
    event: 'cancelled',
    data: { generation_id: 'g', progress },
  });
  assert.deepEqual(applied.patch.messages[0].progress, progress);
  assert.deepEqual(applied.patch.generation.progress, progress);
  assert.deepEqual(applied.cursor, { lastEventId: 2, replayText: '' });
  assert.deepEqual(state, before);
  assert.deepEqual(cursor, { lastEventId: 0, replayText: '' });
});

it('이벤트 반영은 중복 텍스트와 중단 뒤 delta를 버리고 원본 메시지를 바꾸지 않는다', () => {
  const state = {
    ...INITIAL_STATE,
    messages: [message],
    generation: { id: 'g', assistant_message_id: 'a', status: 'running' },
  };
  const event = {
    id: 2,
    event: 'delta',
    data: { generation_id: 'g', text: '다음' },
  };
  const cursor = { lastEventId: 1, replayText: '앞부분 ' };
  const applied = reduceGenerationEvent(state, cursor, event);
  assert.equal(applied.patch.messages[0].content, '앞부분 다음');
  assert.equal(state.messages[0].content, '확인 질문');
  assert.equal(reduceGenerationEvent(state, applied.cursor, event), null);
  for (const cancelled of [
    { ...state, cancelling: true },
    { ...state, generation: { ...state.generation, cancel_requested: true } },
  ]) {
    const ignored = reduceGenerationEvent(cancelled, cursor, event);
    assert.equal(ignored.patch.messages, undefined);
    assert.deepEqual(ignored.cursor, { lastEventId: 2, replayText: '앞부분 ' });
  }
});
