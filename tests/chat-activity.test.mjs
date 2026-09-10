import assert from 'node:assert/strict';
import { afterEach, it } from 'node:test';
import { ChatStore } from '../features/chat/state/chat-store.ts';
import { validContextStatus } from '../features/chat/state/context-status.ts';

const stores = [];
afterEach(() => {
  for (const store of stores.splice(0)) store.dispose();
});
const tick = () => new Promise((resolve) => setImmediate(resolve));
async function until(predicate) {
  for (let i = 0; i < 50; i++) {
    if (predicate()) return;
    await tick();
  }
  assert.fail('상태 전환이 완료되지 않았습니다.');
}
const context = (id, overrides = {}) => ({
  generation_id: id,
  phase: 'ready',
  input_tokens: 120,
  output_tokens: null,
  max_output_tokens: 512,
  context_window: 4096,
  summary_through_sequence: 4,
  compaction_status: 'completed',
  ...overrides,
});
function harness(override = async () => undefined) {
  const model = {
    active: false,
    status: 'running',
    context: context('g-a'),
    used: 0,
    controller: null,
    signal: null,
    streamCount: 0,
    navigations: [],
  };
  const conversation = (id) => ({
    id,
    workspace_id: id === 'c' ? 'w2' : 'w',
    title: `대화 ${id}`,
    status: 'active',
    model: 'mock',
    is_pinned: false,
    active_generation_id: id === 'a' && model.active ? 'g-a' : null,
  });
  const generation = () => ({
    id: 'g-a',
    conversation_id: 'a',
    assistant_message_id: 'answer-a',
    status: model.status,
    context: model.context,
  });
  const request = async (url, init = {}) => {
    const result = await override(url, init, model);
    if (result !== undefined) return result;
    if (url === '/api/v1/workspaces')
      return Response.json({
        items: [
          { id: 'w', name: '기본' },
          { id: 'w2', name: '두 번째' },
        ],
      });
    if (url === '/api/v1/usage')
      return Response.json({
        unlimited: false,
        remaining_tokens: 1000 - model.used,
        used_tokens: model.used,
      });
    if (url === '/api/v1/generations/active')
      return Response.json({ items: model.active ? [generation()] : [] });
    if (url.startsWith('/api/v1/conversations?'))
      return Response.json({ items: [], next_cursor: null });
    if (url === '/api/v1/generations/g-a') return Response.json(generation());
    if (url === '/api/v1/generations/g-a/cancel') {
      model.active = false;
      model.status = 'cancelled';
      return Response.json(generation());
    }
    if (url.includes('/events?')) {
      model.signal = init.signal;
      model.streamCount++;
      return new Response(
        new ReadableStream({
          start(controller) {
            model.controller = controller;
            init.signal.addEventListener(
              'abort',
              () => {
                try {
                  controller.close();
                } catch {
                  /* 이미 끝난 합성 스트림은 다시 닫지 않는다. */
                }
              },
              { once: true },
            );
          },
        }),
      );
    }
    const match = url.match(
      /^\/api\/v1\/conversations\/([abc])(\/messages\?limit=50)?$/,
    );
    if (match) {
      const id = match[1];
      if (!match[2]) return Response.json(conversation(id));
      return Response.json({
        items: [
          {
            id: `answer-${id}`,
            conversation_id: id,
            role: 'assistant',
            content:
              id === 'a' && model.status === 'completed'
                ? '최종 답변'
                : `원문 ${id}`,
            sequence: 2,
            status: 'completed',
          },
        ],
        next_cursor: null,
        active_generation_id: id === 'a' && model.active ? 'g-a' : null,
        context:
          id === 'a'
            ? model.context
            : context(`g-${id}`, {
                input_tokens: 20,
                summary_through_sequence: 0,
              }),
      });
    }
    assert.fail(`예상하지 않은 요청: ${url}`);
  };
  const store = new ChatStore(request, (id) => model.navigations.push(id));
  stores.push(store);
  model.emit = (id, event, data) =>
    model.controller.enqueue(
      new TextEncoder().encode(
        `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
      ),
    );
  return { store, model };
}

it('대화별 초안과 작업 공간별 새 대화 초안을 왕복 이동해도 보존한다', async () => {
  const { store } = harness();
  await store.initialize();
  store.setDraft('새 대화 초안');
  await store.openConversation('a');
  store.setDraft('A 초안');
  await store.openConversation('b');
  store.setDraft('B 초안');
  await store.openConversation('a');
  assert.equal(store.getSnapshot().draft, 'A 초안');
  store.newDraft();
  assert.equal(store.getSnapshot().draft, '새 대화 초안');
  await store.setWorkspace('w2');
  assert.equal(store.getSnapshot().draft, '');
  store.setDraft('다른 공간 초안');
  await store.setWorkspace('w');
  assert.equal(store.getSnapshot().draft, '새 대화 초안');
  await store.openConversation('b');
  assert.equal(store.getSnapshot().draft, 'B 초안');
  store.dispose();
  await store.initialize('a');
  assert.equal(store.getSnapshot().draft, '');
});

it('다른 대화를 보는 동안 생성·문맥 이벤트를 원래 대화에만 반영하고 한 번 알린다', async () => {
  const { store, model } = harness();
  model.active = true;
  await store.initialize('a');
  await until(() => model.controller);
  store.setDraft('A의 다음 질문');
  await store.openConversation('b');
  store.setDraft('B 작성 중');
  model.emit(1, 'meta', {
    context: context('g-a', { input_tokens: 300 }),
    stage: 'generating',
  });
  model.emit(2, 'delta', { text: '생성 중인 답변' });
  await tick();
  assert.equal(store.getSnapshot().messages[0].content, '원문 b');
  assert.equal(store.getSnapshot().context.input_tokens, 20);
  assert.equal(model.signal.aborted, false);
  assert.equal(await store.send('다른 질문', { thinking: false }), false);
  await store.openConversation('a');
  assert.equal(store.getSnapshot().messages[0].content, '생성 중인 답변');
  assert.equal(store.getSnapshot().context.input_tokens, 300);
  assert.equal(store.getSnapshot().draft, 'A의 다음 질문');
  assert.equal(model.streamCount, 1);
  await store.openConversation('b');
  model.active = false;
  model.status = 'completed';
  model.used = 30;
  model.context = context('g-a', { phase: 'finished', output_tokens: 10 });
  model.emit(3, 'done', { input_tokens: 120, output_tokens: 10 });
  model.controller.close();
  await until(
    () =>
      store.getSnapshot().notifications.length === 1 &&
      store.getSnapshot().usage.used_tokens === 30,
  );
  assert.equal(store.getSnapshot().tasks.length, 0);
  assert.equal(store.getSnapshot().draft, 'B 작성 중');
  assert.equal(store.getSnapshot().notifications[0].conversationId, 'a');
  const opening = store.openConversation('a');
  assert.equal(store.getSnapshot().notifications.length, 0);
  await opening;
  await store.refresh();
  assert.equal(store.getSnapshot().context.output_tokens, 10);
  assert.equal(store.getSnapshot().messages[0].content, '최종 답변');
  assert.equal(store.getSnapshot().notifications.length, 0);
  store.dismissNotification('g-a');
  await store.refresh();
  assert.equal(store.getSnapshot().notifications.length, 0);
});

it('현재 보고 있는 대화가 완료되면 점을 만들지 않고 이동·재조회 뒤에도 되살리지 않는다', async () => {
  const { store, model } = harness();
  model.active = true;
  await store.initialize('a');
  await until(() => model.controller);
  const unreadCounts = [];
  const unsubscribe = store.subscribe(() =>
    unreadCounts.push(store.getSnapshot().notifications.length),
  );
  model.status = 'completed';
  model.active = false;
  model.context = context('g-a', { phase: 'finished', output_tokens: 10 });
  model.controller.close();
  await until(
    () =>
      store.getSnapshot().generation === null &&
      store.getSnapshot().context.phase === 'finished',
  );
  assert.equal(store.getSnapshot().notifications.length, 0);
  assert.ok(unreadCounts.every((count) => count === 0));
  await store.openConversation('b');
  await store.refresh();
  await store.openConversation('a');
  assert.equal(store.getSnapshot().notifications.length, 0);
  unsubscribe();
});

it('목록에 없는 다른 대화의 활성 작업을 복원하고 현재 화면에서 중단한다', async () => {
  const { store, model } = harness();
  model.active = true;
  await store.initialize('b');
  await until(() => model.controller);
  assert.equal(store.getSnapshot().selected.id, 'b');
  assert.equal(store.getSnapshot().tasks[0].conversationId, 'a');
  store.setDraft('다른 대화 초안');
  await store.cancelTask('a');
  assert.equal(store.getSnapshot().selected.id, 'b');
  assert.equal(store.getSnapshot().draft, '다른 대화 초안');
  assert.equal(store.getSnapshot().tasks.length, 0);
  assert.equal(store.getSnapshot().notifications[0].status, 'cancelled');
});

it('계정 종료 후 늦은 접수 응답은 새 계정의 초안·작업·알림에 섞이지 않는다', async () => {
  let release;
  const { store } = harness((url, init) => {
    if (init.method === 'POST' && url.endsWith('/messages'))
      return new Promise((resolve) => {
        release = resolve;
      });
  });
  await store.initialize('a');
  store.setDraft('이전 사용자 질문');
  const pending = store.send('이전 사용자 질문', { thinking: false });
  await until(() => release);
  store.dispose();
  await store.initialize('b');
  store.setDraft('새 사용자 초안');
  release(
    Response.json({ generation_id: 'g-a', assistant_message_id: 'answer-a' }),
  );
  await pending;
  assert.equal(store.getSnapshot().selected.id, 'b');
  assert.equal(store.getSnapshot().draft, '새 사용자 초안');
  assert.deepEqual(store.getSnapshot().tasks, []);
  assert.deepEqual(store.getSnapshot().notifications, []);
});

it('문맥 수치가 없거나 잘못됐을 때 0으로 바꾸지 않고 마지막 유효한 값을 보존한다', async () => {
  assert.equal(
    validContextStatus(context('g-a', { input_tokens: null })),
    true,
  );
  for (const value of [-1, NaN, '120', 1.5])
    assert.equal(
      validContextStatus(context('g-a', { input_tokens: value })),
      false,
    );
  const { store, model } = harness();
  model.active = true;
  await store.initialize('a');
  await until(() => model.controller);
  model.emit(1, 'meta', {
    context: context('g-a', {
      input_tokens: null,
      summary_through_sequence: null,
      compaction_status: 'running',
    }),
  });
  await tick();
  assert.equal(store.getSnapshot().context.input_tokens, null);
  model.emit(2, 'meta', { context: context('another-generation') });
  model.emit(3, 'meta', { context: context('g-a', { input_tokens: -3 }) });
  await tick();
  assert.equal(store.getSnapshot().context.input_tokens, null);
});

it('새 대화 저장 중 이동했다가 저장이 실패해도 원래 초안으로 돌아갈 수 있다', async () => {
  let release;
  const { store } = harness(async (url, init) => {
    if (url === '/api/v1/conversations' && init.method === 'POST')
      return new Promise((resolve) => {
        release = resolve;
      });
  });
  await store.initialize();
  store.setDraft('저장 전 첫 질문');
  const sending = store.send('저장 전 첫 질문', { thinking: false });
  await until(() => release);
  await store.openConversation('b');
  store.newDraft();
  assert.equal(store.getSnapshot().draft, '저장 전 첫 질문');
  release(
    Response.json(
      { detail: '일시적으로 저장할 수 없습니다.' },
      { status: 503 },
    ),
  );
  await sending;
  assert.equal(store.getSnapshot().draft, '저장 전 첫 질문');
  assert.equal(store.getSnapshot().sending, false);
  assert.match(store.getSnapshot().error, /저장/);
});

it('다른 대화에서 늦게 끝난 사용량 조회가 최신 정산 수치를 되돌리지 않는다', async () => {
  let defer = false;
  let release;
  const { store, model } = harness(async (url) => {
    if (url === '/api/v1/usage' && defer) {
      defer = false;
      return new Promise((resolve) => {
        release = resolve;
      });
    }
  });
  await store.initialize('a');
  defer = true;
  const stale = store.refreshUsage();
  await until(() => release);
  await store.openConversation('b');
  model.used = 100;
  await store.refreshUsage();
  release(
    Response.json({ unlimited: false, used_tokens: 1, remaining_tokens: 999 }),
  );
  await stale;
  assert.equal(store.getSnapshot().usage.used_tokens, 100);
});

it('완료 후 메시지 재조회가 실패해도 생성 조회에서 확인한 최종 문맥을 유지한다', async () => {
  let failMessages = false;
  const { store, model } = harness(async (url) => {
    if (failMessages && url.includes('/messages?'))
      return Response.json({ detail: '일시 오류' }, { status: 503 });
  });
  model.active = true;
  await store.initialize('a');
  await until(() => model.controller);
  model.status = 'completed';
  model.active = false;
  model.context = context('g-a', { phase: 'finished', output_tokens: 20 });
  failMessages = true;
  model.controller.close();
  await until(
    () => store.getSnapshot().generation === null && store.getSnapshot().error,
  );
  assert.equal(store.getSnapshot().context.phase, 'finished');
  assert.equal(store.getSnapshot().context.output_tokens, 20);
});
