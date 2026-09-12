import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  MemoryStore,
  parseMemories,
} from '../features/memory/state/memory-store.ts';

const id = '11111111-1111-4111-8111-111111111111';
const item = {
  id,
  key: '직업',
  content: '개발자',
  updated_at: '2026-09-12T00:00:00Z',
};
const value = (revision = 0, items = []) => ({ revision, limit: 20, items });
const response = (data) => Response.json(data);
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

describe('개인 기억 상태', () => {
  it('서버 조회와 명시적인 저장·수정·삭제에만 요청하고 세대를 전달한다', async () => {
    const calls = [];
    const replies = [
      value(),
      value(1, [item]),
      value(2, [{ ...item, content: '백엔드 개발자' }]),
      value(3),
    ];
    const store = new MemoryStore(async (path, init) => {
      calls.push([path, init]);
      return response(replies.shift());
    });
    await store.load();
    assert.equal(await store.save(' 직업 ', ' 개발자 '), true);
    assert.deepEqual(JSON.parse(calls[1][1].body), {
      revision: 0,
      key: '직업',
      content: '개발자',
    });
    assert.equal(calls[1][1].method, 'POST');
    assert.equal(await store.save('직업', '백엔드 개발자', id), true);
    assert.equal(calls[2][0], `/api/v1/memories/${id}`);
    assert.equal(calls[2][1].method, 'PATCH');
    assert.equal(await store.remove(id), true);
    assert.deepEqual(JSON.parse(calls[3][1].body), { revision: 2 });
    assert.equal(calls[3][1].method, 'DELETE');
    assert.deepEqual(store.getSnapshot().value, value(3));
  });
  it('중복 전송과 저장 중 새로고침을 막는다', async () => {
    const pending = deferred();
    let calls = 0;
    const store = new MemoryStore(async () =>
      ++calls === 1 ? response(value()) : pending.promise,
    );
    await store.load();
    const saving = store.save('직업', '개발자');
    assert.equal(await store.save('직업', '개발자'), false);
    await store.load();
    assert.equal(calls, 2);
    pending.resolve(response(value(1, [item])));
    assert.equal(await saving, true);
  });
  it('계정 전환·화면 닫기 뒤 도착한 조회와 저장 응답을 버린다', async () => {
    for (const mutation of [false, true]) {
      const pending = deferred();
      let first = true;
      const store = new MemoryStore(async () => {
        if (mutation && first) {
          first = false;
          return response(value());
        }
        return pending.promise;
      });
      let request;
      if (mutation) {
        await store.load();
        request = store.save('직업', '개발자');
      } else request = store.load();
      store.dispose();
      pending.resolve(response(value(1, [item])));
      await request;
      assert.equal(store.getSnapshot().value, null);
    }
  });
  it('느린 옛 조회로 최신 목록을 덮어쓰지 않는다', async () => {
    const pending = deferred();
    let count = 0;
    const store = new MemoryStore(async () =>
      ++count === 1 ? pending.promise : response(value(2)),
    );
    const old = store.load();
    await store.load();
    pending.resolve(response(value(1, [item])));
    await old;
    assert.deepEqual(store.getSnapshot().value, value(2));
  });
  it('충돌 시 성공으로 표시하지 않고 새로고침으로 복구한다', async () => {
    let count = 0;
    const store = new MemoryStore(async () =>
      ++count === 2
        ? Response.json({}, { status: 409 })
        : response(value(count - 1)),
    );
    await store.load();
    assert.equal(await store.save('직업', '개발자'), false);
    assert.match(store.getSnapshot().error, /새로고침/);
    assert.equal(store.getSnapshot().value.revision, 0);
    await store.load();
    assert.equal(store.getSnapshot().error, null);
    assert.equal(store.getSnapshot().value.revision, 2);
  });
  it('조회 실패·잘못된 응답에서는 저장할 수 없다', async () => {
    const store = new MemoryStore(async () => response({ revision: 0 }));
    await store.load();
    assert.equal(store.getSnapshot().loading, false);
    assert.ok(store.getSnapshot().error);
    assert.equal(await store.save('직업', '개발자'), false);
    for (const invalid of [
      null,
      value(-1),
      { ...value(), limit: 999 },
      value(1, [{ ...item, id: '../other' }]),
      value(1, [{ ...item, content: '' }]),
      value(1, [item, item]),
    ])
      assert.throws(() => parseMemories(invalid));
  });
});
