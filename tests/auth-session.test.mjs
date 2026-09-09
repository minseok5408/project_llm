import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';

import { AuthSessionStore } from '../features/auth/state/auth-session.ts';

const HOUR = 60 * 60 * 1000;
const session = (overrides = {}) => ({
  user: {
    id: 'user-1',
    email: 'user@example.com',
    display_name: '테스트 사용자',
    platform_role: 'system',
  },
  expires_at: new Date(Date.now() + 24 * HOUR).toISOString(),
  csrf_token: 'memory-only-csrf',
  ...overrides,
});

describe('메모리 기반 로그인 상태', { concurrency: false }, () => {
  let store;
  let originalFetch;

  beforeEach(() => {
    store = new AuthSessionStore();
    originalFetch = globalThis.fetch;
    globalThis.fetch = async () => assert.fail('Unexpected request');
  });

  afterEach(() => {
    store.dispose();
    globalThis.fetch = originalFetch;
  });

  it('첫 화면은 확인 중이며 /me의 401만 로그인 화면 상태로 바꾼다', async () => {
    assert.equal(store.getSnapshot().status, 'checking');
    globalThis.fetch = async (url, init) => {
      assert.equal(url, '/api/v1/auth/me');
      assert.equal(init.credentials, 'same-origin');
      assert.equal(init.cache, 'no-store');
      return new Response(null, { status: 401 });
    };
    await store.verify();
    assert.equal(store.getSnapshot().status, 'anonymous');
    await assert.rejects(store.request('/api/status'), { name: 'AbortError' });
  });

  it('503 또는 네트워크 실패는 로그인/채팅 대신 재시도 상태로 처리한다', async () => {
    for (const mode of ['503', 'network']) {
      globalThis.fetch = async () => {
        if (mode === 'network') throw new TypeError('Failed to fetch');
        return new Response(null, { status: 503 });
      };
      await store.verify();
      assert.equal(store.getSnapshot().status, 'unavailable');
    }
    globalThis.fetch = async () => Response.json(session());
    await store.verify();
    assert.equal(store.getSnapshot().status, 'authenticated');
  });

  it('로그인 요청은 JSON이며 로그인 세션에서 받은 CSRF만 쓰기 요청에 붙인다', async () => {
    const value = session();
    globalThis.fetch = async (url, init) => {
      if (url === '/api/v1/auth/login') {
        assert.equal(init.method, 'POST');
        assert.equal(init.credentials, 'same-origin');
        assert.equal(
          new Headers(init.headers).get('content-type'),
          'application/json',
        );
        assert.deepEqual(JSON.parse(init.body), {
          email: value.user.email,
          password: 'temporary-test-password',
        });
        return Response.json(value);
      }
      assert.equal(url, '/api/chat');
      assert.equal(
        new Headers(init.headers).get('x-csrf-token'),
        value.csrf_token,
      );
      assert.equal(init.credentials, 'same-origin');
      return Response.json({ ok: true });
    };
    await store.authenticate('login', {
      email: value.user.email,
      password: 'temporary-test-password',
    });
    assert.deepEqual(store.getSnapshot().session, value);
    await store.request('/api/chat', {
      method: 'POST',
      headers: { 'X-CSRF-Token': 'untrusted' },
    });
  });

  it('8자 비밀번호로 가입한 사용자의 이름과 24시간 세션을 바로 반영한다', async () => {
    const value = session({
      user: {
        id: 'new-user',
        email: 'new@example.com',
        display_name: '새 사용자 <테스트>',
        platform_role: 'member',
      },
    });
    globalThis.fetch = async (url, init) => {
      assert.equal(url, '/api/v1/auth/signup');
      assert.deepEqual(JSON.parse(init.body), {
        email: 'new@example.com',
        password: 'test1234',
        display_name: '새 사용자 <테스트>',
      });
      assert.equal(init.credentials, 'same-origin');
      return Response.json(value);
    };
    await store.authenticate('signup', {
      email: 'new@example.com',
      password: 'test1234',
      display_name: '새 사용자 <테스트>',
    });
    assert.equal(store.getSnapshot().status, 'authenticated');
    assert.equal(
      store.getSnapshot().session.user.display_name,
      '새 사용자 <테스트>',
    );
    assert.equal(store.getSnapshot().session.expires_at, value.expires_at);
    assert.equal(store.getSnapshot().session.user.platform_role, 'member');
  });

  it('로그인 24시간 절대 만료는 /me 재확인으로 연장되지 않는다', async (context) => {
    context.mock.timers.enable({
      apis: ['Date', 'setTimeout'],
      now: Date.now(),
    });
    const value = session();
    globalThis.fetch = async () => Response.json(value);
    await store.verify();
    context.mock.timers.tick(12 * HOUR);
    await store.verify();
    context.mock.timers.tick(12 * HOUR - 1);
    assert.equal(store.getSnapshot().status, 'authenticated');
    context.mock.timers.tick(1);
    assert.equal(store.getSnapshot().status, 'anonymous');
    assert.match(store.getSnapshot().message, /24시간/);
  });

  it('401은 동시에 진행 중인 요청을 모두 취소하고 세션 정보를 지운다', async () => {
    globalThis.fetch = async () => Response.json(session());
    await store.verify();
    const signals = [];
    globalThis.fetch = async (_url, init) => {
      signals.push(init.signal);
      return new Response(null, { status: signals.length === 2 ? 401 : 200 });
    };
    await store.request('/api/chat', { method: 'POST' });
    await store.request('/api/status');
    assert.equal(store.getSnapshot().status, 'anonymous');
    assert.equal('session' in store.getSnapshot(), false);
    assert.ok(signals.every((signal) => signal.aborted));
  });

  it('focus 재확인 중 들어온 API 401도 세션을 무효화한다', async () => {
    globalThis.fetch = async () => Response.json(session());
    await store.verify();
    let resolveVerify;
    globalThis.fetch = async (url) => {
      if (url === '/api/v1/auth/me') {
        return new Promise((resolve) => {
          resolveVerify = resolve;
        });
      }
      return new Response(null, { status: 401 });
    };
    const verifying = store.verify();
    await store.request('/api/status');
    resolveVerify(Response.json(session()));
    await verifying;
    assert.equal(store.getSnapshot().status, 'anonymous');
  });

  it('같은 사용자의 쿠키 세션이 바뀌면 과거 요청을 취소하고 뒤늦은 401은 무시한다', async () => {
    const oldSession = session();
    globalThis.fetch = async () => Response.json(oldSession);
    await store.verify();
    let oldSignal;
    let resolveOldRequest;
    globalThis.fetch = async (_url, init) => {
      oldSignal = init.signal;
      return new Promise((resolve) => {
        resolveOldRequest = resolve;
      });
    };
    const oldRequest = store.request('/api/status');
    const newSession = session({ csrf_token: 'new-session-csrf' });
    globalThis.fetch = async () => Response.json(newSession);
    await store.verify();
    assert.equal(oldSignal.aborted, true);
    resolveOldRequest(new Response(null, { status: 401 }));
    await oldRequest;
    assert.equal(store.getSnapshot().status, 'authenticated');
    assert.equal(store.getSnapshot().session.csrf_token, 'new-session-csrf');
  });

  it('비밀번호 제출 중 focus 이벤트는 로그인 요청을 취소하지 않는다', async () => {
    let resolveLogin;
    let loginSignal;
    let requestCount = 0;
    globalThis.fetch = async (_url, init) => {
      requestCount += 1;
      loginSignal = init.signal;
      return new Promise((resolve) => {
        resolveLogin = resolve;
      });
    };
    const login = store.authenticate('login', {
      email: 'user@example.com',
      password: 'temporary-test-password',
    });
    await store.verify();
    assert.equal(requestCount, 1);
    assert.equal(loginSignal.aborted, false);
    resolveLogin(Response.json(session()));
    await login;
    assert.equal(store.getSnapshot().status, 'authenticated');
  });

  it('로그인 화면의 focus 재확인은 입력 폼을 로딩 화면으로 교체하지 않는다', async () => {
    globalThis.fetch = async () => new Response(null, { status: 401 });
    await store.verify();
    let resolveVerify;
    globalThis.fetch = async () =>
      new Promise((resolve) => {
        resolveVerify = resolve;
      });
    const verifying = store.verify();
    assert.equal(store.getSnapshot().status, 'anonymous');
    resolveVerify(new Response(null, { status: 401 }));
    await verifying;
    assert.equal(store.getSnapshot().status, 'anonymous');
  });

  it('생성 중지 신호를 전달하며 로그아웃 후에도 모든 생성 신호가 취소된다', async () => {
    globalThis.fetch = async () => Response.json(session());
    await store.verify();
    const signals = [];
    globalThis.fetch = async (url, init) => {
      signals.push(init.signal);
      if (url === '/api/v1/auth/logout-all') {
        assert.equal(init.method, 'POST');
        assert.equal(
          new Headers(init.headers).get('x-csrf-token'),
          'memory-only-csrf',
        );
      }
      return new Response(null, { status: 204 });
    };
    const stop = new AbortController();
    await store.request('/api/chat', { method: 'POST', signal: stop.signal });
    stop.abort();
    assert.equal(signals[0].aborted, true);
    await store.request('/api/chat', { method: 'POST' });
    await store.logout(true);
    assert.equal(store.getSnapshot().status, 'anonymous');
    assert.ok(signals.every((signal) => signal.aborted));
  });

  it('실패한 로그아웃은 완료로 표시하지 않고 다시 시도할 수 있다', async () => {
    globalThis.fetch = async () => Response.json(session());
    await store.verify();
    globalThis.fetch = async () => new Response(null, { status: 503 });
    await store.logout();
    assert.equal(store.getSnapshot().status, 'authenticated');
    assert.equal(store.getSnapshot().busy, false);
    assert.match(store.getSnapshot().message, /실패/);
    globalThis.fetch = async () => new Response(null, { status: 204 });
    await store.logout();
    assert.equal(store.getSnapshot().status, 'anonymous');
  });

  it('재확인 실패는 기존 세션과 요청을 정리한다', async () => {
    globalThis.fetch = async () => Response.json(session());
    await store.verify();
    let activeSignal;
    globalThis.fetch = async (_url, init) => {
      activeSignal = init.signal;
      return new Response(null, { status: 200 });
    };
    await store.request('/api/status');
    globalThis.fetch = async () => new Response(null, { status: 503 });
    await store.verify();
    assert.equal(store.getSnapshot().status, 'unavailable');
    assert.equal(activeSignal.aborted, true);
  });

  it('만료된 서버 세션과 잘못된 응답은 채팅에 노출하지 않는다', async () => {
    globalThis.fetch = async () =>
      Response.json(
        session({ expires_at: new Date(Date.now() - 1).toISOString() }),
      );
    await store.verify();
    assert.equal(store.getSnapshot().status, 'anonymous');
    globalThis.fetch = async () => Response.json({ user: {} });
    await store.verify();
    assert.equal(store.getSnapshot().status, 'unavailable');
  });
});
