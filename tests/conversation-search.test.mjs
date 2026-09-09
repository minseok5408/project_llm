import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { ConversationSearchStore } from '../features/chat/state/conversation-search-store.ts';

const page = (ids, cursor = null) =>
  Response.json({
    items: ids.map((id) => ({ id, title: id, status: 'active' })),
    next_cursor: cursor,
  });

describe('저장된 채팅 검색', () => {
  it('대화 목록 초기화 전에는 요청하지 않고 대기 상태를 끝낸다', async () => {
    const store = new ConversationSearchStore(async () => {
      throw new Error('호출되면 안 됩니다.');
    }, '');
    await store.initialize();
    assert.ok(store.getSnapshot().error);
    store.setQuery('검색');
    await store.search();
    assert.equal(store.getSnapshot().loading, false);
    assert.ok(store.getSnapshot().error);
    store.dispose();
  });
  it('현재 작업 공간의 활성·보관 대화를 조회하고 검색어를 안전하게 전달한다', async () => {
    const requests = [];
    const store = new ConversationSearchStore(async (url) => {
      requests.push(new URL(url, 'http://localhost'));
      return page(['result']);
    }, 'workspace');
    await store.initialize();
    assert.equal(requests[0].searchParams.get('workspace_id'), 'workspace');
    assert.equal(requests[0].searchParams.get('status'), 'all');
    store.setQuery('  한글 %_ / &  ');
    await store.search();
    assert.equal(requests[1].searchParams.get('q'), '한글 %_ / &');
    assert.equal(store.getSnapshot().items[0].id, 'result');
    store.dispose();
  });

  it('검색어 변경 후 늦게 도착한 이전 결과와 닫힌 화면의 응답을 무시한다', async () => {
    const pending = [];
    const store = new ConversationSearchStore(
      (url, options) =>
        new Promise((resolve) => {
          pending.push({ resolve, signal: options.signal, url });
        }),
      'workspace',
    );
    const initial = store.initialize();
    store.setQuery('최신 검색');
    const latest = store.search();
    assert.equal(pending[0].signal.aborted, true);
    pending[1].resolve(page(['latest']));
    await latest;
    pending[0].resolve(page(['old']));
    await initial;
    assert.deepEqual(
      store.getSnapshot().items.map((item) => item.id),
      ['latest'],
    );
    store.setQuery('닫힌 검색');
    const closed = store.search();
    store.dispose();
    pending[2].resolve(page(['closed']));
    await closed;
    assert.equal(pending[2].signal.aborted, true);
    assert.equal(store.getSnapshot().items.length, 0);
  });

  it('추가 페이지 실패 시 기존 결과와 커서를 보존해 재시도한다', async () => {
    let calls = 0;
    const queries = [];
    const store = new ConversationSearchStore(async (url) => {
      queries.push(new URL(url, 'http://localhost').searchParams);
      calls += 1;
      if (calls === 1) return page(['one', 'two'], 'next-page');
      if (calls === 2) return new Response(null, { status: 503 });
      return page(['two', 'three']);
    }, 'workspace');
    await store.initialize();
    await store.search(true);
    assert.deepEqual(
      store.getSnapshot().items.map((item) => item.id),
      ['one', 'two'],
    );
    assert.equal(store.getSnapshot().nextCursor, 'next-page');
    assert.ok(store.getSnapshot().error);
    await store.search(true);
    assert.equal(queries[2].get('cursor'), 'next-page');
    assert.deepEqual(
      store.getSnapshot().items.map((item) => item.id),
      ['one', 'two', 'three'],
    );
    assert.equal(store.getSnapshot().nextCursor, null);
    assert.equal(store.getSnapshot().error, null);
    store.dispose();
  });

  it('닫았다 다시 열면 이전 검색어와 결과를 초기화한다', async () => {
    const store = new ConversationSearchStore(
      async () => page(['recent']),
      'workspace',
    );
    await store.initialize();
    store.setQuery('이전 검색어');
    store.dispose();
    await store.initialize();
    assert.equal(store.getSnapshot().query, '');
    assert.deepEqual(
      store.getSnapshot().items.map((item) => item.id),
      ['recent'],
    );
    assert.equal(store.getSnapshot().loading, false);
    store.dispose();
  });
});
