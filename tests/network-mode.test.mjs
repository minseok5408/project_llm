import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';
import { NetworkModeStore } from '../features/network/state/network-mode-store.ts';

const online = {
  local_only: false,
  revision: 1,
  mode: 'online',
  reason: 'available',
  search_configured: true,
  checked_at: '2026-09-09T01:00:00Z',
};
const local = {
  ...online,
  local_only: true,
  mode: 'local',
  reason: 'forced_local',
  revision: 2,
};
const offline = { ...online, mode: 'local', reason: 'offline' };
const DATA_ON = { network_mode: 'auto', web_search: 'auto' };
const DATA_OFF = { network_mode: 'local', web_search: 'off' };
const tick = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('사용자별 데이터 사용 설정과 검색 연결 상태', () => {
  const stores = [];
  const create = (request) => {
    const store = new NetworkModeStore(request);
    stores.push(store);
    return store;
  };
  afterEach(() => stores.splice(0).forEach((store) => store.dispose()));

  it('초기 설정 확인 전에는 외부 전송을 막고 데이터 사용 ON 확인 후 자동 검색을 허용한다', async () => {
    const calls = [];
    const store = create(async (path, init) => {
      calls.push({ path, ...init });
      return Response.json(online);
    });
    assert.deepEqual(store.generationPolicy(), DATA_OFF);
    assert.equal(store.getSnapshot().value, null);
    await store.initialize();
    assert.equal(store.getSnapshot().value.mode, 'online');
    assert.deepEqual(store.generationPolicy(), DATA_ON);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].path, '/api/v1/network-mode');
    assert.equal(calls[0].method, 'GET');
  });

  it('데이터 사용 ON의 인터넷 불가 상태와 OFF를 구분하고 다음 질문의 재연결을 허용한다', async () => {
    const store = create(async () => Response.json(offline));
    await store.initialize();
    assert.equal(store.getSnapshot().value.local_only, false);
    assert.equal(store.getSnapshot().value.mode, 'local');
    assert.deepEqual(store.generationPolicy(), DATA_ON);
  });

  it('데이터 사용 OFF에서는 초기·수동 확인 모두 GET만 보내고 외부 검사 요청을 보내지 않는다', async () => {
    const calls = [];
    const store = create(async (path, init) => {
      calls.push([path, init.method]);
      return Response.json(local);
    });
    await store.initialize();
    await store.check();
    await tick();
    assert.deepEqual(calls, [
      ['/api/v1/network-mode', 'GET'],
      ['/api/v1/network-mode', 'GET'],
    ]);
    assert.deepEqual(store.generationPolicy(), DATA_OFF);
  });

  it('온라인 수동 확인은 인증된 JSON POST로 수행하고 재연결 결과를 반영한다', async () => {
    const calls = [];
    const store = create(async (path, init) => {
      calls.push({ path, ...init });
      return Response.json(calls.length === 1 ? offline : online);
    });
    await store.initialize();
    await store.check();
    assert.equal(calls[1].path, '/api/v1/network-mode/check');
    assert.equal(calls[1].method, 'POST');
    assert.equal(calls[1].headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(calls[1].body), {});
    assert.equal(store.getSnapshot().value.mode, 'online');
  });

  it('데이터 사용 OFF 저장 중 질문을 로컬로 제한하고 성공한 설정만 확정 표시한다', async () => {
    const pending = deferred();
    const calls = [];
    const store = create(async (path, init) => {
      calls.push({ path, ...init });
      return init.method === 'PATCH' ? pending.promise : Response.json(online);
    });
    await store.initialize();
    const saving = store.setDataUsage(false);
    assert.equal(store.getSnapshot().saving, true);
    assert.equal(store.getSnapshot().value.local_only, false);
    assert.deepEqual(store.generationPolicy(), DATA_OFF);
    assert.deepEqual(JSON.parse(calls[1].body), { local_only: true });
    pending.resolve(Response.json(local));
    await saving;
    assert.equal(store.getSnapshot().value.local_only, true);
    assert.equal(store.getSnapshot().saving, false);
    assert.equal(calls.length, 2);
  });

  it('OFF 저장에 실패하면 기존 ON 선택을 보존하되 재확인 전에는 로컬로 답한다', async () => {
    const store = create(async (_path, init) =>
      init.method === 'PATCH'
        ? Response.json(
            { detail: '설정을 저장하지 못했습니다.' },
            { status: 503 },
          )
        : Response.json(online),
    );
    await store.initialize();
    await store.setDataUsage(false);
    assert.equal(store.getSnapshot().value.local_only, false);
    assert.match(store.getSnapshot().error, /저장하지 못했/);
    assert.deepEqual(store.generationPolicy(), DATA_OFF);
    await store.check();
    assert.equal(store.getSnapshot().error, null);
    assert.deepEqual(store.generationPolicy(), DATA_ON);
  });

  it('서버 오류·잘못된 응답에서도 데이터 사용 ON으로 오인하지 않는다', async () => {
    for (const value of [
      null,
      {},
      { ...online, mode: 'wifi' },
      { ...online, checked_at: '잘못된 시각' },
    ]) {
      const store = create(async () => Response.json(value));
      await store.initialize();
      assert.equal(store.getSnapshot().value, null);
      assert.equal(store.getSnapshot().loading, false);
      assert.ok(store.getSnapshot().error);
      assert.deepEqual(store.generationPolicy(), DATA_OFF);
    }
  });

  it('검색 연결 검사 중 데이터 사용을 끄면 늦은 검사 결과를 무시한다', async () => {
    const pending = deferred();
    let checkSignal;
    const store = create(async (path, init) => {
      if (path.endsWith('/check')) {
        checkSignal = init.signal;
        return pending.promise;
      }
      return Response.json(init.method === 'PATCH' ? local : online);
    });
    await store.initialize();
    const checking = store.check();
    await store.setDataUsage(false);
    assert.equal(checkSignal.aborted, true);
    pending.resolve(Response.json(online));
    await checking;
    assert.equal(store.getSnapshot().value.local_only, true);
    assert.equal(store.getSnapshot().checking, false);
    assert.deepEqual(store.generationPolicy(), DATA_OFF);
  });

  it('설정 쓰기는 직렬화해 동시에 반대 설정을 저장하지 않는다', async () => {
    const pending = deferred();
    let writes = 0;
    const store = create(async (_path, init) => {
      if (init.method === 'PATCH') {
        writes += 1;
        return pending.promise;
      }
      return Response.json(online);
    });
    await store.initialize();
    const saving = store.setDataUsage(false);
    await store.setDataUsage(true);
    await store.check();
    assert.equal(writes, 1);
    pending.resolve(Response.json(local));
    await saving;
    assert.equal(store.getSnapshot().value.local_only, true);
  });

  it('데이터 사용 ON 저장은 local_only를 끄고 자동 검색을 허용한다', async () => {
    const calls = [];
    const store = create(async (_path, init) => {
      calls.push(init);
      return Response.json(init.method === 'PATCH' ? online : local);
    });
    await store.initialize();
    await store.setDataUsage(true);
    assert.deepEqual(store.generationPolicy(), DATA_ON);
    assert.deepEqual(
      calls.map((call) => call.method),
      ['GET', 'PATCH'],
    );
    assert.deepEqual(JSON.parse(calls[1].body), { local_only: false });
  });

  it('화면이 사라지면 요청을 취소하고 뒤늦은 응답이 설정을 바꾸지 않는다', async () => {
    const pending = deferred();
    let signal;
    const store = create(async (_path, init) => {
      signal = init.signal;
      return pending.promise;
    });
    const initializing = store.initialize();
    store.dispose();
    assert.equal(signal.aborted, true);
    pending.resolve(Response.json(online));
    await initializing;
    assert.equal(store.getSnapshot().value, null);
    assert.deepEqual(store.generationPolicy(), DATA_OFF);
  });

  it('화면 재연결과 계정별 새 store에 이전 초기화 응답·데이터 사용 선택을 섞지 않는다', async () => {
    const pending = deferred();
    let calls = 0;
    const store = create(async () =>
      ++calls === 1 ? pending.promise : Response.json(local),
    );
    const first = store.initialize();
    store.dispose();
    await store.initialize();
    pending.resolve(Response.json(online));
    await first;
    assert.equal(store.getSnapshot().value.local_only, true);
    assert.deepEqual(store.generationPolicy(), DATA_OFF);
    const other = create(async () => Response.json(online));
    assert.equal(other.getSnapshot().value, null);
    assert.deepEqual(other.generationPolicy(), DATA_OFF);
  });

  it('저장한 데이터 사용 선택은 새 화면에서도 같은 검색 요청 정책으로 복원된다', async () => {
    let saved = local;
    const request = async (_path, init) => {
      if (init.method === 'PATCH')
        saved = JSON.parse(init.body).local_only ? local : online;
      return Response.json(saved);
    };
    const store = create(request);
    await store.initialize();
    assert.deepEqual(store.generationPolicy(), DATA_OFF);
    await store.setDataUsage(true);
    assert.deepEqual(store.generationPolicy(), DATA_ON);
    const restored = create(request);
    await restored.initialize();
    assert.deepEqual(restored.generationPolicy(), DATA_ON);
    await restored.setDataUsage(false);
    const restoredOff = create(request);
    await restoredOff.initialize();
    assert.deepEqual(restoredOff.generationPolicy(), DATA_OFF);
  });

  it('데이터 사용 ON 저장이나 연결 확인 중에도 완료 전까지 외부 검색을 막는다', async () => {
    for (const operation of ['save', 'check']) {
      const pending = deferred();
      const store = create(async (_path, init) =>
        init.method === 'GET'
          ? Response.json(operation === 'save' ? local : online)
          : pending.promise,
      );
      await store.initialize();
      const updating =
        operation === 'save' ? store.setDataUsage(true) : store.check();
      assert.deepEqual(store.generationPolicy(), DATA_OFF);
      pending.resolve(Response.json(online));
      await updating;
      assert.deepEqual(store.generationPolicy(), DATA_ON);
    }
  });

  it('생성에서 확인된 검색 실패·재연결을 표시하되 데이터 사용 선택은 그대로 보존한다', async () => {
    let calls = 0;
    const store = create(async (_path, init) => {
      calls += 1;
      return Response.json(init.method === 'PATCH' ? local : online);
    });
    const completed = {
      status: 'completed',
      reason: null,
      provider: 'brave',
      sources: [],
    };
    await store.initialize();
    store.observeSearch({
      ...completed,
      status: 'unavailable',
      reason: 'offline',
    });
    assert.equal(store.getSnapshot().value.mode, 'local');
    assert.equal(store.getSnapshot().value.local_only, false);
    store.observeSearch(completed);
    assert.equal(store.getSnapshot().value.mode, 'online');
    assert.equal(store.getSnapshot().value.revision, online.revision);
    assert.equal(calls, 1);
    await store.setDataUsage(false);
    store.observeSearch(completed);
    assert.equal(store.getSnapshot().value.mode, 'local');
    assert.equal(store.getSnapshot().value.local_only, true);
    assert.equal(calls, 2);
  });
});
