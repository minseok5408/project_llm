import assert from 'node:assert/strict';
import { register } from 'node:module';
import { it } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ChatStore } from '../features/chat/state/chat-store.ts';

register('./helpers/tsx-loader.mjs', import.meta.url);
const { HighlightedText, ConversationSearchResult } =
  await import('../features/chat/components/conversation-search-result.tsx');

const conversation = (id) => ({
  id,
  workspace_id: 'workspace',
  title: id,
  status: 'active',
  is_pinned: false,
  model: 'test',
  last_message_at: '2026-09-12T00:00:00Z',
  created_at: '2026-09-12T00:00:00Z',
});
const messages = (first, last, id = 'a') =>
  Array.from({ length: last - first + 1 }, (_, index) => ({
    id: `${id}-${first + index}`,
    sequence: first + index,
    conversation_id: id,
    role: 'user',
    content: `메시지 ${first + index}`,
    status: 'completed',
    created_at: '',
    token_count: null,
  }));
const page = (first, last, older = null, newer = null) =>
  Response.json({
    items: messages(first, last),
    next_cursor: older,
    newer_cursor: newer,
  });
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
async function setup(t, override = async () => undefined) {
  const records = new Map(['a', 'b'].map((id) => [id, conversation(id)]));
  const calls = [];
  const navigation = [];
  const request = async (url, init = {}) => {
    calls.push({ url, ...init });
    const custom = await override(url, init);
    if (custom !== undefined) return custom;
    if (url === '/api/v1/workspaces')
      return Response.json({
        items: [{ id: 'workspace', name: '공간', role: 'owner' }],
      });
    if (url === '/api/v1/usage') return Response.json({ unlimited: true });
    if (url === '/api/v1/generations/active')
      return Response.json({ items: [] });
    if (url.startsWith('/api/v1/conversations?'))
      return Response.json({
        items: [...records.values()].filter((item) => item.status === 'active'),
        next_cursor: null,
      });
    if (url.includes('/files'))
      return Response.json({ items: [], enabled: false });
    const messageMatch = url.match(/\/conversations\/(a|b)\/messages/);
    if (messageMatch)
      return Response.json({
        items: messages(101, 150, messageMatch[1]),
        next_cursor: 101,
        newer_cursor: null,
      });
    const id = url.split('/').at(-1);
    if (init.method === 'DELETE') {
      records.delete(id);
      return new Response(null, { status: 204 });
    }
    if (init.method === 'PATCH')
      records.set(id, { ...records.get(id), ...JSON.parse(init.body) });
    if (records.has(id)) return Response.json(records.get(id));
    return Response.json(
      { detail: '대상을 찾을 수 없습니다.' },
      { status: 404 },
    );
  };
  const store = new ChatStore(request, (id) => navigation.push(id));
  t.after(() => store.dispose());
  await store.initialize('a');
  store.setDraft('작성 중인 초안');
  return { store, calls, navigation, records };
}

it('검색 강조는 정규식 기호와 HTML도 원문으로 유지한다', () => {
  const html = renderToStaticMarkup(
    createElement(HighlightedText, {
      text: '<script>hi</script> (x)+ 및 (x)+',
      query: '(x)+',
    }),
  );
  assert.equal((html.match(/<mark /g) ?? []).length, 2);
  assert.match(html, /&lt;script&gt;hi&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.equal(
    renderToStaticMarkup(
      createElement(HighlightedText, { text: '안녕', query: ' ' }),
    ),
    '안녕',
  );
});

it('검색 결과는 일치 문장을 보여주고 정확한 메시지 식별자를 전달한다', () => {
  const target = {
    ...conversation('a'),
    search_match: {
      message_id: 'a-30',
      snippet: '찾던 문장입니다.',
      role: 'user',
      sequence: 30,
    },
  };
  const calls = [];
  const element = ConversationSearchResult({
    conversation: target,
    query: '문장',
    onOpen: (...args) => calls.push(args),
  });
  const html = renderToStaticMarkup(element);
  assert.match(html, /일치한 메시지/);
  assert.match(html, /<mark[^>]*>문장<\/mark>/);
  element.props.onClick();
  assert.deepEqual(calls, [['a', 'a-30']]);
  ConversationSearchResult({
    conversation: conversation('b'),
    query: 'b',
    onOpen: (...args) => calls.push(args),
  }).props.onClick();
  assert.deepEqual(calls[1], ['b', undefined]);
});

it('열지 않은 대화의 이름·고정·보관을 바꿔도 현재 대화와 초안을 유지한다', async (t) => {
  const { store, navigation, calls } = await setup(t);
  const original = store.getSnapshot().messages;
  assert.equal(await store.updateConversation({ title: '새 제목' }, 'b'), true);
  assert.equal(await store.updateConversation({ is_pinned: true }, 'b'), true);
  assert.equal(
    await store.updateConversation({ status: 'archived' }, 'b'),
    true,
  );
  assert.equal(store.getSnapshot().selected.id, 'a');
  assert.equal(store.getSnapshot().draft, '작성 중인 초안');
  assert.equal(store.getSnapshot().messages, original);
  assert.equal(
    store.getSnapshot().conversations.some((item) => item.id === 'b'),
    false,
  );
  assert.deepEqual(navigation, []);
  assert.ok(
    calls
      .filter((call) => call.method === 'PATCH')
      .every((call) => call.url.endsWith('/b')),
  );
});

it('다른 대화의 삭제 실패는 대상을 보존하고 성공은 현재 초안과 화면을 유지한다', async (t) => {
  let blocked = true;
  const { store, navigation } = await setup(t, async (_url, init) => {
    if (init.method === 'DELETE' && blocked)
      return Response.json({ detail: '생성 중입니다.' }, { status: 409 });
  });
  assert.equal(await store.deleteConversation('b'), false);
  assert.ok(store.getSnapshot().conversations.some((item) => item.id === 'b'));
  blocked = false;
  assert.equal(await store.deleteConversation('b'), true);
  assert.equal(store.getSnapshot().selected.id, 'a');
  assert.equal(store.getSnapshot().draft, '작성 중인 초안');
  assert.deepEqual(navigation, []);
});

it('삭제 대상을 응답 대기 중 열어도 완료 뒤 삭제된 화면을 남기지 않는다', async (t) => {
  const pending = deferred();
  const { store } = await setup(t, async (_url, init) => {
    if (init.method === 'DELETE') return pending.promise;
  });
  const deletion = store.deleteConversation('b');
  await store.openConversation('b');
  pending.resolve(new Response(null, { status: 204 }));
  assert.equal(await deletion, true);
  assert.equal(store.getSnapshot().selected, null);
});

it('검색 위치 주변을 직접 열고 앞뒤 페이지와 최신 화면을 누락 없이 복원한다', async (t) => {
  const { store, calls } = await setup(t, async (url) => {
    if (url.includes('around=')) return page(26, 75, 26, 75);
    if (url.includes('before=26')) return page(1, 25);
    if (url.includes('after=75')) return page(76, 125, 76, 125);
  });
  assert.equal(await store.revealMessage('a-50'), true);
  assert.equal(store.getSnapshot().messages[0].sequence, 26);
  assert.equal(store.getSnapshot().newerMessageCursor, 75);
  const requests = calls.filter((call) =>
    call.url.includes('/messages?'),
  ).length;
  await store.refresh();
  assert.equal(
    calls.filter((call) => call.url.includes('/messages?')).length,
    requests,
  );
  await store.olderMessages();
  await store.newerMessages();
  assert.deepEqual(
    store.getSnapshot().messages.map((item) => item.sequence),
    Array.from({ length: 125 }, (_, index) => index + 1),
  );
  assert.equal(store.getSnapshot().messageCursor, null);
  assert.equal(store.getSnapshot().newerMessageCursor, 125);
  assert.equal(await store.latestMessages(), true);
  assert.equal(store.getSnapshot().messages[0].sequence, 101);
  assert.equal(store.getSnapshot().newerMessageCursor, null);
  assert.equal(store.getSnapshot().draft, '작성 중인 초안');
});

it('이미 읽은 메시지는 다시 가져오지 않고 이전 검색의 늦은 응답은 최신 창을 덮지 않는다', async (t) => {
  const pending = deferred();
  const { store, calls } = await setup(t, async (url) => {
    if (url.includes('around=')) return pending.promise;
  });
  assert.equal(await store.revealMessage('a-120'), true);
  assert.equal(
    calls.some((call) => call.url.includes('around=')),
    false,
  );
  const search = store.revealMessage('a-10');
  await store.latestMessages();
  pending.resolve(page(1, 35, null, 35));
  assert.equal(await search, false);
  assert.equal(store.getSnapshot().messages[0].sequence, 101);
});

it('검색 대상이 삭제되거나 접근할 수 없으면 현재 메시지를 보존한다', async (t) => {
  const { store } = await setup(t, async (url) => {
    if (url.includes('around='))
      return Response.json(
        { detail: '대상을 찾을 수 없습니다.' },
        { status: 404 },
      );
  });
  const original = store.getSnapshot().messages;
  assert.equal(await store.revealMessage('a-10'), false);
  assert.equal(store.getSnapshot().messages, original);
  assert.match(store.getSnapshot().error, /대상을 찾을 수 없습니다/);
});

it('화면에 있는 메시지를 다시 선택해도 대기 중인 이전 검색 응답을 무효화한다', async (t) => {
  const pending = deferred();
  const { store } = await setup(t, async (url) => {
    if (url.includes('around=')) return pending.promise;
  });
  const previous = store.revealMessage('a-10');
  assert.equal(await store.revealMessage('a-120'), true);
  pending.resolve(page(1, 35, null, 35));
  assert.equal(await previous, false);
  assert.equal(store.getSnapshot().messages[0].sequence, 101);
});

it('불러오는 중인 대화를 다시 열면 검색 이동 전에 첫 조회 완료를 기다린다', async (t) => {
  const pending = deferred();
  const { store } = await setup(t, async (url, init) => {
    if (url === '/api/v1/conversations/b' && !init.method)
      return pending.promise;
  });
  const first = store.openConversation('b');
  let finished = false;
  const second = store.openConversation('b').then(() => {
    finished = true;
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(finished, false);
  pending.resolve(Response.json(conversation('b')));
  await Promise.all([first, second]);
  assert.equal(finished, true);
  assert.equal(store.getSnapshot().selected.id, 'b');
});
