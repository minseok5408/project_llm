import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';
import { ChatStore, mergeMessages } from '../features/chat/state/chat-store.ts';
import {
  consumeGenerationEvents,
  parseGenerationEvent,
  requestId,
} from '../features/chat/stream/chat-stream.ts';
import { GraphemeTyper } from '../features/chat/stream/grapheme-typer.ts';

const W = 'b89fc10b-c0e3-49fb-9d37-a7a16b8a9b3f';
const C = 'd8402e8f-040a-4385-91cc-238d0c65d355';
const G = 'a0ecceec-681c-4baf-b826-9e3e38a7c71d';
const conversation = {
  id: C,
  workspace_id: W,
  title: '저장된 대화',
  status: 'active',
  is_pinned: false,
  model: 'local-model',
  last_message_at: '2026-09-09T01:00:00Z',
  created_at: '2026-09-09T01:00:00Z',
  active_generation_id: null,
};
const message = (id, sequence, content, role = 'assistant') => ({
  id,
  conversation_id: C,
  sequence,
  content,
  role,
  status: 'completed',
  token_count: 2,
  created_at: '2026-09-09T01:00:00Z',
});
const generation = {
  id: G,
  status: 'running',
  assistant_message_id: 'assistant-1',
  cancel_requested: false,
};
const balance = {
  unlimited: false,
  token_limit: 1000,
  used_tokens: 10,
  reserved_tokens: 0,
  remaining_tokens: 990,
  starts_at: null,
  ends_at: null,
};
const encode = (id, event, data) =>
  `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const bytes = (text) => new TextEncoder().encode(text);
const tick = () => new Promise((resolve) => setImmediate(resolve));
async function until(predicate) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (predicate()) return;
    await tick();
  }
  assert.fail('Expected state transition did not occur');
}

describe('저장된 대화와 생성 복원', { concurrency: false }, () => {
  const stores = [];
  afterEach(() => {
    for (const store of stores.splice(0)) store.dispose();
  });
  function create(
    override = async () => undefined,
    navigate = () => {},
    networkPolicy,
  ) {
    const request = async (url, init = {}) => {
      const special = await override(String(url), init);
      if (special !== undefined) return special;
      if (url === '/api/v1/workspaces')
        return Response.json({
          items: [{ id: W, name: '기본 공간', role: 'owner' }],
        });
      if (url === '/api/v1/usage') return Response.json(balance);
      if (url === '/api/v1/generations/active')
        return Response.json({ items: [] });
      if (String(url).startsWith('/api/v1/conversations?'))
        return Response.json({ items: [conversation], next_cursor: null });
      if (url === `/api/v1/conversations/${C}`)
        return Response.json(conversation);
      if (String(url).startsWith(`/api/v1/conversations/${C}/messages?`))
        return Response.json({
          items: [
            message('user-1', 1, '질문', 'user'),
            message('assistant-1', 2, '저장된 응답'),
          ],
          next_cursor: null,
          active_generation_id: null,
        });
      assert.fail(`Unexpected request: ${init.method ?? 'GET'} ${url}`);
    };
    const store = new ChatStore(request, navigate, networkPolicy);
    stores.push(store);
    return store;
  }

  it('길이 제한 답변을 이어서 생성할 때 서버 기본 한도를 사용하고 초안을 보존한다', async () => {
    const attempts = [];
    let finishReason = 'length';
    let allowed = true;
    const store = create(async (url, init) => {
      if (url.includes('/messages?'))
        return Response.json({
          items: [
            {
              ...message('assistant-1', 2, '중단된 답변'),
              finish_reason: finishReason,
              is_current: true,
              can_regenerate: allowed,
            },
          ],
          next_cursor: null,
        });
      if (
        url === `/api/v1/conversations/${C}/messages` &&
        init.method === 'POST'
      ) {
        attempts.push(JSON.parse(init.body));
        return Response.json({
          generation_id: G,
          assistant_message_id: 'next-answer',
        });
      }
      if (url === `/api/v1/generations/${G}`)
        return Response.json({ ...generation, status: 'completed' });
    });
    await store.initialize(C);
    store.setDraft('작성 중인 질문');
    assert.equal(
      await store.continueAnswer('assistant-1', { thinking: true }),
      true,
    );
    assert.deepEqual(attempts[0].options, { thinking: true });
    assert.match(attempts[0].content, /중단된 부분부터/);
    assert.equal(store.getSnapshot().draft, '작성 중인 질문');
    finishReason = 'stop';
    await store.initialize(C);
    assert.equal(
      await store.continueAnswer('assistant-1', { thinking: false }),
      false,
    );
    finishReason = 'length';
    allowed = false;
    await store.initialize(C);
    assert.equal(
      await store.continueAnswer('assistant-1', { thinking: false }),
      false,
    );
    assert.equal(attempts.length, 1);
  });

  it('deep link로 DB 메시지와 공간을 복원하고 사용자 한도를 읽는다', async () => {
    const store = create();
    await store.initialize(C);
    await tick();
    assert.equal(store.getSnapshot().selected.id, C);
    assert.equal(store.getSnapshot().workspaceId, W);
    assert.equal(store.getSnapshot().messages[1].content, '저장된 응답');
    assert.equal(store.getSnapshot().usage.remaining_tokens, 990);
  });

  it('실패한 메시지는 입력을 보존하며 동일 본문의 재시도는 같은 키를 사용한다', async () => {
    const attempts = [];
    const store = create(async (url, init) => {
      if (
        url === `/api/v1/conversations/${C}/messages` &&
        init.method === 'POST'
      ) {
        attempts.push({
          body: JSON.parse(init.body),
          key: new Headers(init.headers).get('idempotency-key'),
        });
        return Response.json({ detail: '토큰이 부족합니다.' }, { status: 402 });
      }
    });
    await store.initialize(C);
    store.setDraft('내 질문');
    const options = { thinking: false, max_tokens: 512 };
    assert.equal(await store.send('내 질문', options), false);
    assert.equal(store.getSnapshot().draft, '내 질문');
    assert.match(store.getSnapshot().error, /토큰/);
    await store.send('내 질문', options);
    await store.send('다른 질문', options);
    assert.deepEqual(attempts[0].body, {
      content: '내 질문',
      options,
      network_mode: 'local',
      web_search: 'auto',
    });
    assert.equal('messages' in attempts[0].body, false);
    assert.equal(attempts[0].key, attempts[1].key);
    assert.notEqual(attempts[1].key, attempts[2].key);
  });

  it('새 대화를 저장한 후 메시지를 보내며 접수 실패 시에도 URL과 입력을 보존한다', async () => {
    const navigations = [];
    const store = create(
      async (url, init) => {
        if (url === '/api/v1/conversations' && init.method === 'POST') {
          assert.deepEqual(JSON.parse(init.body), {
            workspace_id: W,
            title: '첫 질문',
          });
          return Response.json(conversation, { status: 201 });
        }
        if (url === `/api/v1/conversations/${C}/messages`)
          return new Response(null, { status: 429 });
      },
      (id) => navigations.push(id),
    );
    await store.initialize();
    store.setDraft('첫 질문');
    await store.send('첫 질문', { thinking: true, max_tokens: 1024 });
    assert.deepEqual(navigations, [C]);
    assert.equal(store.getSnapshot().draft, '첫 질문');
    assert.equal(store.getSnapshot().selected.id, C);
  });

  it('질문과 재생성 모두 현재 검색 설정을 담고 모드 변경은 별도 재시도 키를 사용한다', async () => {
    const attempts = [];
    let policy = { network_mode: 'auto', web_search: 'on' };
    const store = create(
      async (url, init) => {
        if (url.includes('/messages?'))
          return Response.json({
            items: [
              {
                ...message('assistant-1', 2, '답변'),
                generation_id: G,
                can_regenerate: true,
                is_current: true,
              },
            ],
            next_cursor: null,
          });
        if (init.method === 'POST') {
          attempts.push({
            url,
            body: JSON.parse(init.body),
            key: new Headers(init.headers).get('Idempotency-Key'),
          });
          return Response.json({ detail: '연결 실패' }, { status: 503 });
        }
      },
      () => {},
      () => policy,
    );
    await store.initialize(C);
    const options = { thinking: false, max_tokens: 512 };
    await store.send('오늘 날씨', options);
    await store.send('오늘 날씨', options);
    policy = { network_mode: 'local', web_search: 'on' };
    await store.send('오늘 날씨', options);
    assert.deepEqual(attempts[0].body, {
      content: '오늘 날씨',
      options,
      network_mode: 'auto',
      web_search: 'on',
    });
    assert.equal(attempts[0].key, attempts[1].key);
    assert.notEqual(attempts[1].key, attempts[2].key);
    assert.equal(attempts[2].body.network_mode, 'local');
    await store.regenerate('assistant-1', options);
    policy = { network_mode: 'auto', web_search: 'off' };
    await store.regenerate('assistant-1', options);
    assert.deepEqual(attempts[3].body, {
      options,
      network_mode: 'local',
      web_search: 'on',
    });
    assert.deepEqual(attempts[4].body, {
      options,
      network_mode: 'auto',
      web_search: 'off',
    });
    assert.notEqual(attempts[3].key, attempts[4].key);
    assert.equal(attempts[3].url, `/api/v1/generations/${G}/regenerate`);
  });

  it('검색 단계·출처 SSE를 반영하고 중복·잘못된 메타를 무시하며 완료 뒤 DB 출처를 복원한다', async () => {
    let stream;
    let done = false;
    const searching = {
      status: 'searching',
      reason: null,
      provider: 'brave',
      sources: [],
    };
    const completed = {
      ...searching,
      status: 'completed',
      sources: [
        {
          number: 1,
          title: '공식 자료',
          url: 'https://example.com',
          snippet: '내용',
          retrieved_at: '2026-09-09T01:00:00Z',
        },
      ],
    };
    const store = create(async (url) => {
      if (url.includes('/messages?'))
        return Response.json({
          items: [
            {
              ...message('assistant-1', 2, done ? '검색한 답변' : ''),
              search: done ? completed : null,
            },
          ],
          next_cursor: null,
          active_generation_id: done ? null : G,
        });
      if (url === `/api/v1/generations/${G}`)
        return Response.json({
          ...generation,
          status: done ? 'completed' : 'running',
        });
      if (url.includes('/events?'))
        return new Response(
          new ReadableStream({
            start(controller) {
              stream = controller;
              controller.enqueue(
                bytes(
                  encode(1, 'meta', { stage: 'searching', search: searching }),
                ),
              );
            },
          }),
        );
    });
    await store.initialize(C);
    await until(() => store.getSnapshot().generation?.stage === 'searching');
    assert.deepEqual(store.getSnapshot().generation.search, searching);
    stream.enqueue(
      bytes(
        encode(2, 'meta', { search: { status: 'completed', sources: '오류' } }),
      ),
    );
    await tick();
    assert.deepEqual(store.getSnapshot().generation.search, searching);
    stream.enqueue(
      bytes(
        encode(3, 'meta', { stage: 'generating', search: completed }) +
          encode(4, 'delta', { text: '검색한 답변' }),
      ),
    );
    await until(() => store.getSnapshot().generation?.stage === 'generating');
    assert.deepEqual(store.getSnapshot().generation.search, completed);
    assert.deepEqual(store.getSnapshot().messages[0].search, completed);
    stream.enqueue(
      bytes(encode(2, 'meta', { stage: 'searching', search: searching })),
    );
    await tick();
    assert.equal(store.getSnapshot().generation.stage, 'generating');
    done = true;
    stream.enqueue(bytes(encode(5, 'done', { finish_reason: 'stop' })));
    stream.close();
    await until(() => store.getSnapshot().generation === null);
    await store.refresh();
    assert.deepEqual(store.getSnapshot().messages[0].search, completed);
    assert.equal(store.getSnapshot().messages[0].content, '검색한 답변');
  });

  it('진행 중 응답은 DB 부분 본문에 덧붙이지 않고 이벤트를 처음부터 재생한다', async () => {
    let streamController;
    let streamSignal;
    const store = create(async (url, init) => {
      if (url.includes('/messages?'))
        return Response.json({
          items: [message('assistant-1', 2, 'DB 부분 본문')],
          next_cursor: null,
          active_generation_id: G,
        });
      if (url === `/api/v1/generations/${G}`) return Response.json(generation);
      if (url.includes('/events?')) {
        assert.match(url, /after=0$/);
        streamSignal = init.signal;
        return new Response(
          new ReadableStream({
            start(controller) {
              streamController = controller;
              controller.enqueue(
                bytes(
                  encode(1, 'meta', { assistant_message_id: 'assistant-1' }) +
                    encode(2, 'delta', { text: '복원한 응답' }),
                ),
              );
            },
          }),
          { headers: { 'Content-Type': 'text/event-stream' } },
        );
      }
    });
    await store.initialize(C);
    await until(
      () => store.getSnapshot().messages[0]?.content === '복원한 응답',
    );
    assert.equal(
      store.getSnapshot().messages[0].content.includes('DB 부분'),
      false,
    );
    store.newDraft();
    assert.equal(streamSignal.aborted, false);
    assert.equal(store.getSnapshot().messages.length, 0);
    assert.equal(store.getSnapshot().tasks[0].generation.id, G);
    await store.openConversation(C);
    assert.equal(store.getSnapshot().messages[0].content, '복원한 응답');
    streamController.close();
  });

  it('재연결은 마지막 이벤트 번호 이후만 요청하고 중복 delta를 무시한다', async () => {
    let controller;
    let streams = 0;
    let terminal = false;
    const snapshots = [];
    const store = create(async (url, init) => {
      if (url.includes('/messages?'))
        return Response.json({
          items: [message('assistant-1', 2, terminal ? '안녕하세요' : '')],
          next_cursor: null,
          active_generation_id: terminal ? null : G,
        });
      if (url === `/api/v1/generations/${G}`)
        return Response.json({
          ...generation,
          status: terminal ? 'completed' : 'running',
        });
      if (url.includes('/events?')) {
        streams += 1;
        if (streams === 1)
          return new Response(
            new ReadableStream({
              start(value) {
                controller = value;
                value.enqueue(
                  bytes(
                    encode(1, 'meta', {}) +
                      encode(2, 'delta', { text: '안녕' }),
                  ),
                );
              },
            }),
          );
        assert.match(url, /after=2$/);
        assert.equal(new Headers(init.headers).get('last-event-id'), '2');
        terminal = true;
        return new Response(
          bytes(
            encode(2, 'delta', { text: '안녕' }) +
              encode(3, 'delta', { text: '하세요' }) +
              encode(4, 'done', { input_tokens: 2, output_tokens: 3 }),
          ),
        );
      }
    });
    store.subscribe(() =>
      snapshots.push(store.getSnapshot().messages[0]?.content),
    );
    await store.initialize(C);
    await until(() => store.getSnapshot().messages[0]?.content === '안녕');
    controller.error(new Error('connection lost'));
    await until(() => store.getSnapshot().stream === 'reconnecting');
    await store.reconnect();
    await until(() => store.getSnapshot().generation === null);
    assert.equal(store.getSnapshot().messages[0].content, '안녕하세요');
    assert.equal(
      snapshots.some((text) => text?.includes('안녕안녕')),
      false,
    );
  });

  it('실행 중 취소는 JSON API로 요청하고 최종 정산까지 생성 상태를 유지한다', async () => {
    let controller;
    let terminal = false;
    let cancelCalls = 0;
    const store = create(async (url, init) => {
      if (url.includes('/messages?'))
        return Response.json({
          items: [
            {
              ...message('assistant-1', 2, terminal ? '부분 응답' : ''),
              status: terminal ? 'cancelled' : 'pending',
            },
          ],
          next_cursor: null,
          active_generation_id: terminal ? null : G,
        });
      if (url === `/api/v1/generations/${G}`)
        return Response.json({
          ...generation,
          status: terminal ? 'cancelled' : 'running',
        });
      if (url.endsWith('/cancel')) {
        cancelCalls += 1;
        assert.equal(init.method, 'POST');
        assert.deepEqual(JSON.parse(init.body), {});
        assert.equal(
          new Headers(init.headers).get('content-type'),
          'application/json',
        );
        return Response.json({ ...generation, cancel_requested: true });
      }
      if (url.includes('/events?'))
        return new Response(
          new ReadableStream({
            start(value) {
              controller = value;
              value.enqueue(bytes(encode(1, 'delta', { text: '부분 응답' })));
            },
          }),
        );
    });
    await store.initialize(C);
    await until(() => store.getSnapshot().messages[0]?.content === '부분 응답');
    await store.cancel();
    assert.equal(cancelCalls, 1);
    assert.equal(store.getSnapshot().generation.cancel_requested, true);
    controller.enqueue(bytes(encode(2, 'delta', { text: '숨길 후속 텍스트' })));
    await tick();
    assert.equal(store.getSnapshot().messages[0].content, '부분 응답');
    assert.equal(
      await store.send('추가 질문', { thinking: false, max_tokens: 512 }),
      false,
    );
    terminal = true;
    controller.close();
    await until(() => store.getSnapshot().generation === null);
    assert.equal(store.getSnapshot().messages[0].status, 'cancelled');
  });

  it('대화 정리 단계와 생성 단계를 표시하고 길이 제한 안내를 해당 답변에 남긴다', async () => {
    let controller;
    let terminal = false;
    const store = create(async (url) => {
      if (url.includes('/messages?'))
        return Response.json({
          items: [message('assistant-1', 2, terminal ? '이어질 답변' : '')],
          next_cursor: null,
          active_generation_id: terminal ? null : G,
        });
      if (url === `/api/v1/generations/${G}`)
        return Response.json({
          ...generation,
          status: terminal ? 'completed' : 'running',
        });
      if (url.includes('/events?'))
        return new Response(
          new ReadableStream({
            start(value) {
              controller = value;
              value.enqueue(
                bytes(
                  encode(1, 'meta', { status: 'running', stage: 'compacting' }),
                ),
              );
            },
          }),
        );
    });
    await store.initialize(C);
    await until(() => store.getSnapshot().generation?.stage === 'compacting');
    assert.deepEqual(store.getSnapshot().lengthLimitedMessageIds, []);
    controller.enqueue(bytes(encode(2, 'meta', { status: 'running' })));
    await tick();
    assert.equal(store.getSnapshot().generation.stage, 'compacting');
    controller.enqueue(
      bytes(
        encode(3, 'meta', {
          status: 'running',
          stage: 'generating',
          context_compacted: true,
        }) + encode(4, 'delta', { text: '이어질 답변' }),
      ),
    );
    await until(() => store.getSnapshot().generation?.stage === 'generating');
    assert.equal(store.getSnapshot().generation.context_compacted, true);
    assert.equal(store.getSnapshot().messages[0].content, '이어질 답변');
    terminal = true;
    controller.enqueue(bytes(encode(5, 'done', { finish_reason: 'length' })));
    controller.close();
    await until(() => store.getSnapshot().generation === null);
    assert.deepEqual(store.getSnapshot().lengthLimitedMessageIds, [
      'assistant-1',
    ]);
    await store.refresh();
    assert.deepEqual(store.getSnapshot().lengthLimitedMessageIds, [
      'assistant-1',
    ]);
    store.newDraft();
    assert.deepEqual(store.getSnapshot().lengthLimitedMessageIds, []);
    await store.openConversation(C);
    assert.deepEqual(store.getSnapshot().lengthLimitedMessageIds, []);
  });

  it('대화 정리 중에도 기존 중단 API로 생성 전체를 중단할 수 있다', async () => {
    let controller;
    let cancelled = false;
    const store = create(async (url, init) => {
      if (url.includes('/messages?'))
        return Response.json({
          items: [message('assistant-1', 2, '')],
          next_cursor: null,
          active_generation_id: cancelled ? null : G,
        });
      if (url === `/api/v1/generations/${G}`) return Response.json(generation);
      if (url.endsWith('/cancel')) {
        assert.equal(init.method, 'POST');
        cancelled = true;
        return Response.json({
          ...generation,
          status: 'cancelled',
          cancel_requested: true,
        });
      }
      if (url.includes('/events?'))
        return new Response(
          new ReadableStream({
            start(value) {
              controller = value;
              value.enqueue(bytes(encode(1, 'meta', { stage: 'compacting' })));
            },
          }),
        );
    });
    await store.initialize(C);
    await until(() => store.getSnapshot().generation?.stage === 'compacting');
    await store.cancel();
    assert.equal(cancelled, true);
    assert.equal(store.getSnapshot().generation, null);
    assert.deepEqual(store.getSnapshot().lengthLimitedMessageIds, []);
    controller.close();
  });

  for (const finishReason of [undefined, null, 'stop', 'unknown']) {
    it(`종료 이유 ${String(finishReason)}에서는 길이 제한을 추정하지 않는다`, async () => {
      let terminal = false;
      const store = create(async (url) => {
        if (url.includes('/messages?'))
          return Response.json({
            items: [message('assistant-1', 2, '답변')],
            next_cursor: null,
            active_generation_id: terminal ? null : G,
          });
        if (url === `/api/v1/generations/${G}`)
          return Response.json({
            ...generation,
            status: terminal ? 'completed' : 'running',
          });
        if (url.includes('/events?')) {
          terminal = true;
          return new Response(
            bytes(
              encode(1, 'meta', { status: 'running', stage: 'unknown' }) +
                encode(2, 'delta', { text: '답변' }) +
                encode(3, 'done', {
                  finish_reason: finishReason,
                  output_tokens: 1024,
                }),
            ),
          );
        }
      });
      await store.initialize(C);
      await until(() => store.getSnapshot().generation === null);
      assert.deepEqual(store.getSnapshot().lengthLimitedMessageIds, []);
      assert.equal(store.getSnapshot().messages[0].content, '답변');
    });
  }

  it('중지 요청 응답이 오기 전부터 새 delta를 무시하고 다음 질문 초안을 보존한다', async () => {
    let streamController;
    let resolveCancel;
    const store = create(async (url) => {
      if (url.includes('/messages?'))
        return Response.json({
          items: [message('assistant-1', 2, '')],
          next_cursor: null,
          active_generation_id: G,
        });
      if (url === `/api/v1/generations/${G}`) return Response.json(generation);
      if (url.endsWith('/cancel'))
        return new Promise((resolve) => {
          resolveCancel = resolve;
        });
      if (url.includes('/events?'))
        return new Response(
          new ReadableStream({
            start(controller) {
              streamController = controller;
              controller.enqueue(
                bytes(encode(1, 'delta', { text: '여기까지' })),
              );
            },
          }),
        );
    });
    await store.initialize(C);
    await until(() => store.getSnapshot().messages[0]?.content === '여기까지');
    const cancelling = store.cancel();
    assert.equal(store.getSnapshot().cancelling, true);
    store.setDraft('다음 질문');
    streamController.enqueue(
      bytes(encode(2, 'delta', { text: '이 부분은 표시하지 않음' })),
    );
    await tick();
    assert.equal(store.getSnapshot().messages[0].content, '여기까지');
    assert.equal(store.getSnapshot().draft, '다음 질문');
    resolveCancel(Response.json({ ...generation, cancel_requested: true }));
    await cancelling;
    store.dispose();
    streamController.close();
  });

  it('중지 terminal은 최종 메시지 재조회보다 먼저 전송 잠금을 해제한다', async () => {
    let streamController;
    let resolveMessages;
    let cancelled = false;
    const store = create(async (url) => {
      if (url.includes('/messages?')) {
        if (cancelled)
          return new Promise((resolve) => {
            resolveMessages = resolve;
          });
        return Response.json({
          items: [message('assistant-1', 2, '')],
          next_cursor: null,
          active_generation_id: G,
        });
      }
      if (url === `/api/v1/generations/${G}`) return Response.json(generation);
      if (url.endsWith('/cancel')) {
        cancelled = true;
        return Response.json({
          ...generation,
          status: 'usage_pending',
          cancel_requested: true,
        });
      }
      if (url.includes('/events?'))
        return new Response(
          new ReadableStream({
            start(controller) {
              streamController = controller;
            },
          }),
        );
    });
    await store.initialize(C);
    await until(() => store.getSnapshot().stream === 'live');
    const cancelling = store.cancel();
    await until(() => Boolean(resolveMessages));
    assert.equal(store.getSnapshot().generation, null);
    assert.equal(store.getSnapshot().cancelling, false);
    assert.match(store.getSnapshot().error, /사용량 확인/);
    store.setDraft('정산 조회와 무관하게 작성한 질문');
    resolveMessages(
      Response.json({
        items: [{ ...message('assistant-1', 2, ''), status: 'cancelled' }],
        next_cursor: null,
        active_generation_id: null,
      }),
    );
    await cancelling;
    assert.equal(store.getSnapshot().draft, '정산 조회와 무관하게 작성한 질문');
    streamController.close();
  });

  it('이전 대화의 늦은 중지 실패는 새 화면의 초안과 오류 상태를 바꾸지 않는다', async () => {
    let streamController;
    let rejectCancel;
    const store = create(async (url) => {
      if (url.includes('/messages?'))
        return Response.json({
          items: [message('assistant-1', 2, '')],
          next_cursor: null,
          active_generation_id: G,
        });
      if (url === `/api/v1/generations/${G}`) return Response.json(generation);
      if (url.endsWith('/cancel'))
        return new Promise((_resolve, reject) => {
          rejectCancel = reject;
        });
      if (url.includes('/events?'))
        return new Response(
          new ReadableStream({
            start(controller) {
              streamController = controller;
            },
          }),
        );
    });
    await store.initialize(C);
    await until(() => store.getSnapshot().stream === 'live');
    const cancelling = store.cancel();
    await until(() => Boolean(rejectCancel));
    store.newDraft();
    store.setDraft('새 대화의 질문');
    rejectCancel(new Error('Late request failure'));
    await cancelling;
    assert.equal(store.getSnapshot().error, null);
    assert.equal(store.getSnapshot().draft, '새 대화의 질문');
    assert.equal(store.getSnapshot().generation, null);
    streamController.close();
  });

  it('제목·핀·보관은 부분 PATCH이고 삭제는 JSON 빈 객체를 전송한다', async () => {
    const changes = [];
    const store = create(async (url, init) => {
      if (url === `/api/v1/conversations/${C}` && init.method === 'PATCH') {
        const patch = JSON.parse(init.body);
        changes.push(patch);
        return Response.json({ ...conversation, ...patch });
      }
      if (url === `/api/v1/conversations/${C}` && init.method === 'DELETE') {
        assert.deepEqual(JSON.parse(init.body), {});
        return new Response(null, { status: 204 });
      }
    });
    await store.initialize(C);
    await store.updateConversation({ title: '새 제목' });
    await store.updateConversation({ is_pinned: true });
    await store.updateConversation({ status: 'archived' });
    assert.deepEqual(changes, [
      { title: '새 제목' },
      { is_pinned: true },
      { status: 'archived' },
    ]);
    await store.deleteConversation();
    assert.equal(store.getSnapshot().selected, null);
  });

  it('완료된 요청의 재접수 결과는 DB로 복원하고 생성을 다시 연결하지 않는다', async () => {
    const store = create(async (url, init) => {
      if (
        url === `/api/v1/conversations/${C}/messages` &&
        init.method === 'POST'
      ) {
        return Response.json(
          {
            generation_id: G,
            user_message_id: 'user-1',
            assistant_message_id: 'assistant-1',
            events_url: `/api/v1/generations/${G}/events`,
          },
          { status: 202 },
        );
      }
      if (url === `/api/v1/generations/${G}`)
        return Response.json({ ...generation, status: 'completed' });
      if (url.includes('/events'))
        assert.fail('Terminal generation must not be restarted');
    });
    await store.initialize(C);
    store.setDraft('이미 접수한 질문');
    assert.equal(
      await store.send('이미 접수한 질문', {
        thinking: false,
        max_tokens: 512,
      }),
      true,
    );
    assert.equal(store.getSnapshot().draft, '');
    assert.equal(store.getSnapshot().generation, null);
    assert.equal(store.getSnapshot().messages[1].content, '저장된 응답');
  });

  it('다른 기기에서 취소한 생성은 DB 부분 응답을 유지하며 정산을 기다린다', async () => {
    let controller;
    const store = create(async (url) => {
      if (url.includes('/messages?'))
        return Response.json({
          items: [message('assistant-1', 2, '이미 저장된 부분')],
          next_cursor: null,
          active_generation_id: G,
        });
      if (url === `/api/v1/generations/${G}`)
        return Response.json({ ...generation, cancel_requested: true });
      if (url.includes('/events?'))
        return new Response(
          new ReadableStream({
            start(value) {
              controller = value;
              value.enqueue(
                bytes(
                  encode(1, 'meta', { status: 'running' }) +
                    encode(2, 'delta', { text: '과거 이벤트' }),
                ),
              );
            },
          }),
        );
    });
    await store.initialize(C);
    await until(() => store.getSnapshot().stream === 'live');
    await tick();
    assert.equal(store.getSnapshot().generation.cancel_requested, true);
    assert.equal(store.getSnapshot().messages[0].content, '이미 저장된 부분');
    store.dispose();
    controller.close();
  });

  it('실패 답변을 다시 시도해도 질문·이전 답변·작성 중 입력을 보존하고 요청 키를 재사용한다', async () => {
    const nextId = 'b01a96ba-0d8f-457f-936f-eed1fcd5b92e';
    const attempts = [];
    let accepted = false;
    const original = {
      ...message('assistant-1', 2, '이전 부분 답변'),
      status: 'failed',
      generation_status: 'failed',
      generation_id: G,
      is_current: true,
      can_regenerate: true,
    };
    const store = create(async (url, init) => {
      if (url.includes('/messages?'))
        return Response.json({
          items: [
            message('user-1', 1, '내 질문', 'user'),
            { ...original, is_current: !accepted, can_regenerate: !accepted },
            ...(accepted
              ? [
                  {
                    ...message('assistant-2', 3, '새 답변'),
                    generation_id: nextId,
                    is_current: true,
                    can_regenerate: true,
                    generation_status: 'completed',
                    finish_reason: 'length',
                  },
                ]
              : []),
          ],
          next_cursor: null,
          active_generation_id: null,
        });
      if (url === `/api/v1/generations/${G}/regenerate`) {
        attempts.push({
          body: JSON.parse(init.body),
          key: new Headers(init.headers).get('idempotency-key'),
        });
        if (attempts.length === 1) throw new TypeError('연결 끊김');
        accepted = true;
        return Response.json(
          {
            generation_id: nextId,
            user_message_id: 'user-1',
            assistant_message_id: 'assistant-2',
            events_url: `/api/v1/generations/${nextId}/events`,
          },
          { status: 202 },
        );
      }
      if (url === `/api/v1/generations/${nextId}`)
        return Response.json({
          ...generation,
          id: nextId,
          status: 'completed',
          assistant_message_id: 'assistant-2',
        });
    });
    await store.initialize(C);
    store.setDraft('다음에 보낼 질문');
    const options = { thinking: true, max_tokens: 1024 };
    assert.equal(await store.regenerate('assistant-1', options), false);
    assert.equal(store.getSnapshot().draft, '다음에 보낼 질문');
    assert.equal(await store.regenerate('assistant-1', options), true);
    assert.equal(attempts[0].key, attempts[1].key);
    assert.deepEqual(attempts[0].body, {
      options,
      network_mode: 'local',
      web_search: 'auto',
    });
    const state = store.getSnapshot();
    assert.equal(
      state.messages.filter((item) => item.role === 'user').length,
      1,
    );
    assert.equal(state.messages[1].content, '이전 부분 답변');
    assert.equal(state.messages[1].is_current, false);
    assert.equal(state.messages[2].content, '새 답변');
    assert.equal(state.messages[2].finish_reason, 'length');
    assert.equal(state.draft, '다음에 보낼 질문');
    assert.equal(state.generation, null);
    assert.equal(await store.regenerate('assistant-1', options), false);
    assert.equal(attempts.length, 2);
  });

  it('재생성의 출력 설정을 바꾸면 새 요청 키를 발급하고 기존 요청과 섞지 않는다', async () => {
    const keys = [];
    const store = create(async (url, init) => {
      if (url.includes('/messages?'))
        return Response.json({
          items: [
            {
              ...message('assistant-1', 2, '원문'),
              generation_id: G,
              is_current: true,
              can_regenerate: true,
            },
          ],
          next_cursor: null,
        });
      if (init.method === 'POST') {
        keys.push(new Headers(init.headers).get('idempotency-key'));
        return Response.json({ detail: '사용량 부족' }, { status: 402 });
      }
    });
    await store.initialize(C);
    await store.regenerate('assistant-1', { thinking: false, max_tokens: 512 });
    await store.regenerate('assistant-1', {
      thinking: false,
      max_tokens: 1024,
    });
    await store.send('새 질문', { thinking: false, max_tokens: 1024 });
    assert.equal(new Set(keys).size, 3);
    assert.equal(store.getSnapshot().messages[0].content, '원문');
  });

  it('재생성 중의 연속 클릭을 차단하고 화면 이동 뒤에도 원래 대화의 접수를 완료한다', async () => {
    let complete;
    let posts = 0;
    let submittedSignal;
    const store = create(async (url, init) => {
      if (url === `/api/v1/generations/${G}`)
        return Response.json({ ...generation, status: 'completed' });
      if (url.includes('/messages?'))
        return Response.json({
          items: [
            {
              ...message('assistant-1', 2, '원문'),
              generation_id: G,
              is_current: true,
              can_regenerate: true,
            },
          ],
          next_cursor: null,
        });
      if (init.method === 'POST') {
        posts += 1;
        submittedSignal = init.signal;
        return new Promise((resolve) => {
          complete = resolve;
        });
      }
    });
    await store.initialize(C);
    const options = { thinking: false, max_tokens: 512 };
    const first = store.regenerate('assistant-1', options);
    assert.equal(await store.regenerate('assistant-1', options), false);
    assert.equal(posts, 1);
    store.newDraft();
    store.setDraft('새 대화 질문');
    complete(
      Response.json(
        {
          generation_id: G,
          user_message_id: 'user-1',
          assistant_message_id: 'assistant-2',
        },
        { status: 202 },
      ),
    );
    assert.equal(await first, true);
    assert.equal(submittedSignal.aborted, false);
    assert.equal(store.getSnapshot().generation, null);
    assert.equal(store.getSnapshot().selected, null);
    assert.equal(store.getSnapshot().draft, '새 대화 질문');
    assert.equal(store.getSnapshot().notifications[0].conversationId, C);
  });

  it('서버가 재생성을 허용하지 않는 메시지와 보관된 대화에서는 요청하지 않는다', async () => {
    let archived = false;
    let allowed = false;
    const store = create(async (url, init) => {
      if (url === `/api/v1/conversations/${C}`)
        return Response.json({
          ...conversation,
          status: archived ? 'archived' : 'active',
        });
      if (url.includes('/messages?'))
        return Response.json({
          items: [
            {
              ...message('assistant-1', 2, '원문'),
              generation_id: G,
              is_current: true,
              can_regenerate: allowed,
            },
          ],
          next_cursor: null,
        });
      if (init.method === 'POST') assert.fail('허용되지 않은 재생성 요청');
    });
    await store.initialize(C);
    const options = { thinking: false, max_tokens: 512 };
    assert.equal(await store.regenerate('assistant-1', options), false);
    assert.equal(await store.regenerate('없는 메시지', options), false);
    allowed = true;
    archived = true;
    await store.openConversation(C);
    assert.equal(await store.regenerate('assistant-1', options), false);
  });

  it('메시지 페이지 병합은 중복 없이 순번을 유지하고 새 DB 값을 우선한다', () => {
    const merged = mergeMessages(
      [message('a', 1, '이전'), message('b', 2, '부분')],
      [message('b', 2, '최종'), message('c', 3, '다음')],
    );
    assert.deepEqual(
      merged.map((item) => item.content),
      ['이전', '최종', '다음'],
    );
  });

  it('SSE의 분할 UTF-8과 CRLF·여러 data 줄을 처리한다', async () => {
    const raw = bytes(
      'id: 1\r\nevent: delta\r\ndata: {\r\ndata: "text":"한글🌿"}\r\n\r\n',
    );
    const events = [];
    await consumeGenerationEvents(
      new Response(
        new ReadableStream({
          start(controller) {
            for (const value of raw)
              controller.enqueue(new Uint8Array([value]));
            controller.close();
          },
        }),
      ),
      (event) => events.push(event),
    );
    assert.equal(events[0].data.text, '한글🌿');
    assert.equal(parseGenerationEvent('id: -1\nevent: delta\ndata: {}'), null);
    assert.equal(parseGenerationEvent('id: 1\ndata: invalid'), null);
    assert.match(
      requestId(),
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });
});

describe('응답 글자 표시', () => {
  it('한글과 조각으로 도착한 이모지를 grapheme 하나씩 표시한다', (context) => {
    context.mock.timers.enable({ apis: ['setTimeout'] });
    const typer = new GraphemeTyper();
    typer.update('\ud83d');
    context.mock.timers.tick(10);
    assert.equal(typer.getSnapshot(), '');
    typer.update('👩\u200d');
    context.mock.timers.tick(10);
    assert.equal(typer.getSnapshot(), '');
    typer.update('👩‍💻한');
    context.mock.timers.tick(10);
    assert.equal(typer.getSnapshot(), '👩‍💻');
    typer.update('👩‍💻한글 ');
    context.mock.timers.tick(10);
    assert.equal(typer.getSnapshot(), '👩‍💻한');
    context.mock.timers.tick(10);
    assert.equal(typer.getSnapshot(), '👩‍💻한글');
    typer.complete('👩‍💻한글 ');
    assert.equal(typer.getSnapshot(), '👩‍💻한글 ');
    typer.dispose();
  });

  it('결합 문자와 한글 자모가 다음 조각과 합쳐질 때 분리해 내보내지 않는다', (context) => {
    context.mock.timers.enable({ apis: ['setTimeout'] });
    const typer = new GraphemeTyper();
    typer.update('a');
    context.mock.timers.tick(10);
    typer.update('a\u0301ᄒ');
    context.mock.timers.tick(10);
    assert.equal(typer.getSnapshot(), 'á');
    typer.update('a\u0301하');
    context.mock.timers.tick(10);
    assert.equal(typer.getSnapshot(), 'á');
    typer.update('a\u0301한!');
    context.mock.timers.tick(10);
    assert.equal(typer.getSnapshot(), 'á한');
    typer.dispose();
  });

  it('중지하면 이미 받은 글자 대기열도 즉시 멈추며 완료 응답으로 다시 채우지 않는다', (context) => {
    context.mock.timers.enable({ apis: ['setTimeout'] });
    const typer = new GraphemeTyper();
    typer.update('가나다라마');
    context.mock.timers.tick(10);
    assert.equal(typer.getSnapshot(), '가');
    typer.freeze();
    typer.update('가나다라마바사');
    typer.complete('가나다라마바사');
    context.mock.timers.runAll();
    assert.equal(typer.getSnapshot(), '가');
    typer.dispose();
  });

  it('긴 대기열은 따라잡고 reduced-motion은 확정된 글자를 즉시 표시한다', (context) => {
    context.mock.timers.enable({ apis: ['setTimeout'] });
    const typer = new GraphemeTyper();
    typer.update('가'.repeat(10_000) + '!');
    assert.equal(typer.getSnapshot().length, 10_000 - 128);
    typer.flushStable();
    assert.equal(typer.getSnapshot(), '가'.repeat(10_000));
    typer.complete('가'.repeat(10_000) + '!');
    let notifications = 0;
    typer.subscribe(() => {
      notifications += 1;
    });
    context.mock.timers.runAll();
    assert.equal(notifications, 0);
    typer.dispose();
  });

  it('화면을 떠나면 남은 타이핑 타이머가 이후 상태를 갱신하지 않는다', (context) => {
    context.mock.timers.enable({ apis: ['setTimeout'] });
    const typer = new GraphemeTyper('저장된 대화');
    assert.equal(typer.getSnapshot(), '저장된 대화');
    typer.update('새 응답입니다');
    context.mock.timers.tick(10);
    const visible = typer.getSnapshot();
    typer.dispose();
    context.mock.timers.runAll();
    assert.equal(typer.getSnapshot(), visible);
  });
});
