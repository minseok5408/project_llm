import assert from 'node:assert/strict';
import { it } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  useModelStatus,
  watchModelStatus,
} from '../features/chat/hooks/use-model-status.ts';
import {
  registerChatTool,
  useWebMcp,
} from '../features/chat/hooks/use-web-mcp.ts';
import {
  connectChatNavigation,
  useChatSession,
} from '../features/chat/hooks/use-chat-session.ts';
import { useChatScroll } from '../features/chat/hooks/use-chat-scroll.ts';
import { useWorkbenchMenus } from '../features/chat/hooks/use-workbench-menus.ts';
import { useReducedMotion } from '../features/chat/hooks/use-reduced-motion.ts';

const flush = () => new Promise((resolve) => setImmediate(resolve));

it('서버 렌더링은 브라우저 API나 인증 요청 없이 훅의 초기 화면 상태만 제공한다', () => {
  function Probe() {
    const request = async () => assert.fail('서버 렌더 중 요청하면 안 됨');
    const menus = useWorkbenchMenus();
    const { store, state, networkState } = useChatSession(
      request,
      menus.closeSidebar,
    );
    const runtime = useModelStatus(request);
    const reducedMotion = useReducedMotion();
    const scroll = useChatScroll({
      conversationId: state.selected?.id ?? null,
      messages: state.messages,
      loadOlderMessages: store.olderMessages,
    });
    useWebMcp(async () => assert.fail('서버 렌더 중 도구 호출하면 안 됨'));
    const snapshot = {
      ready: runtime.ready,
      model: runtime.model,
      sidebarOpen: menus.sidebarOpen,
      searchOpen: menus.searchOpen,
      loading: state.loading,
      networkLoading: networkState.loading,
      showLatest: scroll.showLatest,
      loadingOlder: scroll.loadingOlder,
      viewport: scroll.scrollViewport.current,
      reducedMotion,
    };
    return createElement(
      'script',
      { type: 'application/json' },
      JSON.stringify(snapshot),
    );
  }
  const html = renderToStaticMarkup(createElement(Probe));
  const snapshot = JSON.parse(html.match(/<script[^>]*>(.+)<\/script>/)[1]);
  assert.deepEqual(snapshot, {
    ready: false,
    model: 'mlx-community/Qwen3.8-27B-4bit',
    sidebarOpen: false,
    searchOpen: false,
    loading: true,
    networkLoading: true,
    showLatest: false,
    loadingOlder: false,
    viewport: null,
    reducedMotion: false,
  });
});

it('모델 상태는 반복 확인과 연결 오류를 반영하고 종료 후 늦은 응답을 버린다', async (t) => {
  const polls = [];
  const timers = [];
  const cleared = [];
  t.mock.method(globalThis, 'setInterval', (callback, delay) => {
    const timer = { callback, delay };
    timers.push(timer);
    return timer;
  });
  t.mock.method(globalThis, 'clearInterval', (timer) => cleared.push(timer));
  let runtime = { model: '이전 모델' };
  let updates = 0;
  const stop = watchModelStatus(
    (_url, { signal }) =>
      new Promise((resolve, reject) => polls.push({ signal, resolve, reject })),
    (update) => {
      runtime = update(runtime);
      updates += 1;
    },
  );
  try {
    assert.equal(timers[0].delay, 10_000);
    polls[0].resolve(
      Response.json({
        provider: {
          ready: true,
          backend: 'mlx',
          model: '실행 모델',
          detail: '준비됨',
        },
      }),
    );
    await flush();
    assert.equal(runtime.ready, true);
    assert.equal(runtime.backend, 'MLX');
    timers[0].callback();
    polls[1].reject(new Error('연결 실패'));
    await flush();
    assert.equal(runtime.online, false);
    assert.equal(runtime.model, '실행 모델');
    timers[0].callback();
    stop();
    assert.equal(polls[2].signal.aborted, true);
    assert.deepEqual(cleared, [timers[0]]);
    polls[2].resolve(Response.json({ provider: { ready: true } }));
    await flush();
    assert.equal(updates, 2);
    assert.equal(runtime.online, false);
  } finally {
    stop();
  }
});

it('브라우저 뒤로가기·화면 복귀는 해당 동작만 수행하고 종료 후 이벤트를 해제한다', (t) => {
  const browser = new EventTarget();
  browser.location = { pathname: '/chat/11111111-1111-1111-1111-111111111111' };
  const page = new EventTarget();
  page.visibilityState = 'hidden';
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
  const originalDocument = Object.getOwnPropertyDescriptor(
    globalThis,
    'document',
  );
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: browser,
  });
  Object.defineProperty(globalThis, 'document', {
    configurable: true,
    value: page,
  });
  t.after(() => {
    if (originalWindow)
      Object.defineProperty(globalThis, 'window', originalWindow);
    else delete globalThis.window;
    if (originalDocument)
      Object.defineProperty(globalThis, 'document', originalDocument);
    else delete globalThis.document;
  });
  const calls = [];
  const store = Object.fromEntries(
    [
      'initialize',
      'openConversation',
      'newDraft',
      'refresh',
      'refreshFiles',
      'dispose',
    ].map((name) => [name, (...args) => calls.push([name, ...args])]),
  );
  const disconnect = connectChatNavigation(store, () =>
    calls.push(['closeSidebar']),
  );
  assert.deepEqual(calls.shift(), [
    'initialize',
    '11111111-1111-1111-1111-111111111111',
  ]);
  browser.dispatchEvent(new Event('popstate'));
  assert.deepEqual(calls.splice(0), [
    ['openConversation', '11111111-1111-1111-1111-111111111111', false],
    ['closeSidebar'],
  ]);
  browser.location.pathname = '/';
  browser.dispatchEvent(new Event('popstate'));
  assert.deepEqual(calls.splice(0), [['newDraft'], ['closeSidebar']]);
  page.dispatchEvent(new Event('visibilitychange'));
  assert.deepEqual(calls, []);
  page.visibilityState = 'visible';
  page.dispatchEvent(new Event('visibilitychange'));
  browser.dispatchEvent(new Event('focus'));
  assert.deepEqual(calls.splice(0), [
    ['refresh'],
    ['refreshFiles'],
    ['refresh'],
    ['refreshFiles'],
  ]);
  disconnect();
  assert.deepEqual(calls.splice(0), [['dispose']]);
  browser.dispatchEvent(new Event('popstate'));
  browser.dispatchEvent(new Event('focus'));
  page.dispatchEvent(new Event('visibilitychange'));
  assert.deepEqual(calls, []);
});

it('WebMCP는 같은 전송 검사를 사용하고 종료된 계정 화면에서는 요청하지 않는다', async () => {
  let registered;
  let signal;
  const sent = [];
  let allowed = true;
  const stop = registerChatTool(
    {
      registerTool(tool, options) {
        registered = tool;
        signal = options.signal;
      },
    },
    async (text) => {
      sent.push(text);
      return allowed;
    },
  );
  assert.equal(registered.name, 'send_chat_message');
  assert.deepEqual(await registered.execute({ message: '새 질문' }), {
    status: 'accepted',
  });
  assert.deepEqual(sent, ['새 질문']);
  for (const invalid of [
    null,
    {},
    { message: '' },
    { message: ' ' },
    { message: 3 },
    { message: '가'.repeat(100001) },
  ])
    await assert.rejects(registered.execute(invalid), /1~100,000자/);
  assert.deepEqual(sent, ['새 질문']);
  allowed = false;
  await assert.rejects(
    registered.execute({ message: '전송 중' }),
    /전송할 수 없습니다/,
  );
  stop();
  assert.equal(signal.aborted, true);
  await assert.rejects(
    registered.execute({ message: '이전 계정' }),
    /전송할 수 없습니다/,
  );
  assert.deepEqual(sent, ['새 질문', '전송 중']);
});

it('WebMCP 미지원·등록 실패는 화면 채팅을 막지 않는다', async () => {
  for (const context of [
    undefined,
    {
      registerTool() {
        throw new Error('지원하지 않음');
      },
    },
    {
      registerTool() {
        return Promise.reject(new Error('등록 실패'));
      },
    },
  ]) {
    const stop = registerChatTool(context, async () =>
      assert.fail('등록 중 전송하면 안 됨'),
    );
    stop();
  }
  await flush();
});
