import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';

import { DELETE, GET, PATCH, POST } from '../app/api/[...path]/route.ts';

const LAN_ORIGIN = 'http://192.168.1.20:3000';
const context = (path) => ({ params: Promise.resolve({ path }) });

describe('같은 origin의 로컬 API 중계', { concurrency: false }, () => {
  let originalFetch;
  let originalOrigin;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    originalOrigin = process.env.API_BASE_URL;
    delete process.env.API_BASE_URL;
    // 테스트 중 실제 네트워크 요청이 발생하면 즉시 실패시킨다.
    globalThis.fetch = async () => assert.fail('Unexpected upstream request');
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    if (originalOrigin === undefined) delete process.env.API_BASE_URL;
    else process.env.API_BASE_URL = originalOrigin;
  });

  it('LAN 요청의 쿠키와 CSRF만 전달하고 위조된 프록시 헤더를 버린다', async () => {
    let captured;
    globalThis.fetch = async (url, init) => {
      captured = new Request(url, init);
      return Response.json(
        { provider: { ready: true } },
        {
          headers: {
            'Set-Cookie': 'project_llm_session=value; HttpOnly; SameSite=Lax',
          },
        },
      );
    };

    const response = await GET(
      new Request(`${LAN_ORIGIN}/api/status?detail=all`, {
        headers: {
          Origin: LAN_ORIGIN,
          Host: '192.168.1.20:3000',
          Accept: 'application/json',
          Cookie: 'session=private',
          Authorization: 'Bearer private',
          'Proxy-Authorization': 'Basic private',
          'X-API-Key': 'private',
          'X-CSRF-Token': 'csrf-value',
          'X-Project-LLM-Origin': 'http://attacker.example',
          'X-Project-LLM-Client-IP': '203.0.113.2',
          'X-Forwarded-For': '203.0.113.3',
          Forwarded: 'for=203.0.113.4',
        },
      }),
      context(['status']),
    );

    assert.equal(captured.url, 'http://127.0.0.1:8000/api/status?detail=all');
    assert.equal(captured.method, 'GET');
    assert.equal(captured.headers.get('accept'), 'application/json');
    for (const name of [
      'authorization',
      'proxy-authorization',
      'x-api-key',
      'host',
      'x-project-llm-client-ip',
      'x-forwarded-for',
      'forwarded',
    ]) {
      assert.equal(captured.headers.get(name), null);
    }
    assert.equal(captured.headers.get('cookie'), 'session=private');
    assert.equal(captured.headers.get('x-csrf-token'), 'csrf-value');
    assert.equal(captured.headers.get('origin'), LAN_ORIGIN);
    assert.equal(captured.headers.get('x-project-llm-origin'), LAN_ORIGIN);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { provider: { ready: true } });
    assert.deepEqual(response.headers.getSetCookie(), [
      'project_llm_session=value; HttpOnly; SameSite=Lax',
    ]);
    assert.equal(response.headers.get('cache-control'), 'no-store');
  });

  it('POST 본문과 쿼리를 보존하고 요청 취소를 upstream에 전달한다', async () => {
    process.env.API_BASE_URL = 'http://127.0.0.1:18000';
    const controller = new AbortController();
    const payload = { messages: [{ role: 'user', content: '한글 질문 🌿' }] };
    let upstreamSignal;
    let captured;
    let body;
    globalThis.fetch = async (url, init) => {
      upstreamSignal = init.signal;
      captured = new Request(url, init);
      body = await captured.json();
      controller.abort();
      return Response.json({ accepted: true });
    };

    await POST(
      new Request(
        `${LAN_ORIGIN}/api/chat?mode=stream&label=%ED%95%9C%EA%B8%80`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Origin: LAN_ORIGIN },
          body: JSON.stringify(payload),
          signal: controller.signal,
        },
      ),
      context(['chat']),
    );

    const target = new URL(captured.url);
    assert.equal(target.origin, 'http://127.0.0.1:18000');
    assert.equal(target.pathname, '/api/chat');
    assert.equal(target.searchParams.get('mode'), 'stream');
    assert.equal(target.searchParams.get('label'), '한글');
    assert.equal(captured.method, 'POST');
    assert.equal(captured.headers.get('content-type'), 'application/json');
    assert.deepEqual(body, payload);
    assert.equal(upstreamSignal.aborted, true);
  });

  it('네트워크 설정 조회·변경·검사 경로만 허용하고 인증 헤더를 유지한다', async () => {
    const calls = [];
    globalThis.fetch = async (url, init) => {
      const request = new Request(url, init);
      calls.push({
        method: request.method,
        url: request.url,
        csrf: request.headers.get('x-csrf-token'),
        body: request.method === 'GET' ? null : await request.json(),
      });
      return Response.json({ local_only: true });
    };
    for (const { handler, method, suffix, body } of [
      { handler: GET, method: 'GET', suffix: '', body: null },
      {
        handler: PATCH,
        method: 'PATCH',
        suffix: '',
        body: { local_only: true },
      },
      { handler: POST, method: 'POST', suffix: '/check', body: {} },
    ]) {
      const result = await handler(
        new Request(`${LAN_ORIGIN}/api/v1/network-mode${suffix}`, {
          method,
          headers: {
            Origin: LAN_ORIGIN,
            'Content-Type': 'application/json',
            'X-CSRF-Token': 'synthetic-csrf',
          },
          ...(body === null ? {} : { body: JSON.stringify(body) }),
        }),
      );
      assert.equal(result.status, 200);
    }
    assert.deepEqual(
      calls.map((call) => call.method),
      ['GET', 'PATCH', 'POST'],
    );
    assert.deepEqual(calls[1].body, { local_only: true });
    assert.deepEqual(calls[2].body, {});
    for (const call of calls) {
      assert.match(call.url, /^http:\/\/127.0.0.1:8000\/api\/v1\/network-mode/);
      assert.equal(call.csrf, 'synthetic-csrf');
    }
    const wrong = await GET(
      new Request(`${LAN_ORIGIN}/api/v1/network-mode/check`),
    );
    assert.equal(wrong.status, 405);
    assert.equal(calls.length, 3);
  });

  it(
    '완료되지 않은 SSE의 첫 청크를 즉시 반환하고 스트림 취소를 전파한다',
    { timeout: 2_000 },
    async () => {
      let upstreamCancelled = false;
      const firstChunk = 'event: delta\ndata: {"text":"첫 응답"}\n\n';
      globalThis.fetch = async () =>
        new Response(
          new ReadableStream({
            start(stream) {
              stream.enqueue(new TextEncoder().encode(firstChunk));
              // 응답을 닫지 않아 본문 전체를 모으는 구현은 이 테스트를 통과하지 못한다.
            },
            cancel() {
              upstreamCancelled = true;
            },
          }),
          {
            headers: {
              'Content-Type': 'text/event-stream; charset=utf-8',
              'Cache-Control': 'no-cache, no-transform',
            },
          },
        );

      const response = await POST(
        new Request(`${LAN_ORIGIN}/api/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Origin: LAN_ORIGIN },
          body: '{}',
        }),
        context(['chat']),
      );
      assert.match(response.headers.get('content-type'), /^text\/event-stream/);
      assert.equal(
        response.headers.get('cache-control'),
        'no-store, no-transform',
      );
      const reader = response.body.getReader();
      try {
        const chunk = await reader.read();
        assert.equal(chunk.done, false);
        assert.equal(new TextDecoder().decode(chunk.value), firstChunk);
      } finally {
        await reader.cancel('client stopped');
      }
      assert.equal(upstreamCancelled, true);
    },
  );

  it('upstream 연결 실패의 내부 정보를 응답에 노출하지 않는다', async () => {
    globalThis.fetch = async () => {
      throw new Error(
        'Connection failed: http://private:password@127.0.0.1:8000',
      );
    };
    const response = await GET(
      new Request(`${LAN_ORIGIN}/api/status`),
      context(['status']),
    );

    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), {
      detail: 'API gateway unavailable',
    });
    assert.equal(response.headers.get('cache-control'), 'no-store');
  });

  it('허용되지 않은 경로나 메서드는 upstream에 보내지 않는다', async () => {
    let called = false;
    globalThis.fetch = async () => {
      called = true;
      return Response.json({});
    };
    for (const { handler, method, path, expected } of [
      { handler: GET, method: 'GET', path: '/api/admin', expected: 404 },
      { handler: POST, method: 'POST', path: '/api/chat/extra', expected: 404 },
      { handler: GET, method: 'GET', path: '/api/chat', expected: 405 },
      { handler: POST, method: 'POST', path: '/api/status', expected: 405 },
      {
        handler: GET,
        method: 'GET',
        path: '/api/v1/generations/d8402e8f-040a-4385-91cc-238d0c65d355/regenerate',
        expected: 405,
      },
    ]) {
      const response = await handler(
        new Request(`${LAN_ORIGIN}${path}`, { method }),
        context(path.slice('/api/'.length).split('/')),
      );
      assert.equal(response.status, expected);
    }
    assert.equal(called, false);
  });

  it('원격 주소나 경로가 섞인 API_BASE_URL로 요청을 보내지 않는다', async () => {
    let called = false;
    globalThis.fetch = async () => {
      called = true;
      return Response.json({});
    };
    for (const origin of [
      'http://192.168.1.50:8000',
      'https://remote.example',
      'http://127.0.0.1:8000/private',
      'http://private:password@127.0.0.1:8000',
      'http://127.0.0.1:8000?target=remote',
    ]) {
      process.env.API_BASE_URL = origin;
      const response = await GET(
        new Request(`${LAN_ORIGIN}/api/status`),
        context(['status']),
      );
      assert.equal(response.status, 502);
      assert.deepEqual(await response.json(), {
        detail: 'API gateway unavailable',
      });
    }
    assert.equal(called, false);
  });

  it('모든 인증 경로를 허용하고 여러 Set-Cookie 헤더를 따로 보존한다', async () => {
    const cookies = [
      'project_llm_session=opaque; HttpOnly; Path=/; SameSite=Lax',
      '__Host-session=; Max-Age=0; Secure; HttpOnly; Path=/',
    ];
    globalThis.fetch = async () => {
      const headers = new Headers({ 'Cache-Control': 'public, max-age=86400' });
      for (const cookie of cookies) headers.append('Set-Cookie', cookie);
      return Response.json({ ok: true }, { headers });
    };
    for (const action of ['config', 'me', 'login', 'signup', 'logout']) {
      const method = ['config', 'me'].includes(action) ? 'GET' : 'POST';
      const response = await (method === 'GET' ? GET : POST)(
        new Request(`${LAN_ORIGIN}/api/v1/auth/${action}`, {
          method,
          headers: { Origin: LAN_ORIGIN, 'Content-Type': 'application/json' },
          ...(method === 'POST' ? { body: '{}' } : {}),
        }),
      );
      assert.equal(response.status, 200);
      assert.deepEqual(response.headers.getSetCookie(), cookies);
      assert.equal(response.headers.get('cache-control'), 'no-store');
    }
  });

  it('제거된 모든 기기 로그아웃 경로는 upstream에 전달하지 않는다', async () => {
    const response = await POST(
      new Request(`${LAN_ORIGIN}/api/v1/auth/logout-all`, {
        method: 'POST',
        headers: { Origin: LAN_ORIGIN, 'Content-Type': 'application/json' },
        body: '{}',
      }),
    );
    assert.equal(response.status, 404);
  });

  it('POST의 누락 또는 다른 Origin을 JSON 본문과 관계없이 차단한다', async () => {
    for (const path of [
      '/api/chat',
      '/api/v1/auth/login',
      '/api/v1/auth/signup',
      '/api/v1/auth/logout',
      '/api/v1/generations/d8402e8f-040a-4385-91cc-238d0c65d355/regenerate',
    ]) {
      for (const origin of [
        undefined,
        'null',
        'http://attacker.example',
        `${LAN_ORIGIN}/`,
        'http://192.168.1.20:3001',
      ]) {
        const headers = { 'Content-Type': 'application/json' };
        if (origin !== undefined) headers.Origin = origin;
        const response = await POST(
          new Request(`${LAN_ORIGIN}${path}`, {
            method: 'POST',
            headers,
            body: '{}',
          }),
        );
        assert.equal(response.status, 403);
      }
    }
  });

  it('로그인·가입·채팅은 JSON Content-Type만 허용한다', async () => {
    for (const path of [
      '/api/chat',
      '/api/v1/auth/login',
      '/api/v1/auth/signup',
      '/api/v1/generations/d8402e8f-040a-4385-91cc-238d0c65d355/regenerate',
    ]) {
      for (const contentType of [
        undefined,
        'text/plain',
        'application/x-www-form-urlencoded',
        'multipart/form-data',
      ]) {
        const headers = { Origin: LAN_ORIGIN };
        if (contentType) headers['Content-Type'] = contentType;
        const response = await POST(
          new Request(`${LAN_ORIGIN}${path}`, {
            method: 'POST',
            headers,
            body: '{}',
          }),
        );
        assert.equal(response.status, 415);
      }
    }
  });

  it('대화와 생성의 UUID 경로만 허용하며 재시도 키와 이벤트 커서를 보존한다', async () => {
    const id = 'd8402e8f-040a-4385-91cc-238d0c65d355';
    const routes = [
      [GET, 'GET', '/api/v1/workspaces'],
      [GET, 'GET', '/api/v1/usage'],
      [GET, 'GET', '/api/v1/conversations?workspace_id=' + id],
      [POST, 'POST', '/api/v1/conversations'],
      [GET, 'GET', `/api/v1/conversations/${id}`],
      [PATCH, 'PATCH', `/api/v1/conversations/${id}`],
      [DELETE, 'DELETE', `/api/v1/conversations/${id}`],
      [GET, 'GET', `/api/v1/conversations/${id}/messages?before=50`],
      [POST, 'POST', `/api/v1/conversations/${id}/messages`],
      [GET, 'GET', `/api/v1/generations/${id}`],
      [GET, 'GET', `/api/v1/generations/${id}/events?after=27`],
      [POST, 'POST', `/api/v1/generations/${id}/cancel`],
      [POST, 'POST', `/api/v1/generations/${id}/regenerate`],
    ];
    globalThis.fetch = async (url, init) => {
      assert.equal(new Headers(init.headers).get('idempotency-key'), id);
      assert.equal(new Headers(init.headers).get('last-event-id'), '27');
      assert.equal(new URL(url).hostname, '127.0.0.1');
      return Response.json({ ok: true });
    };
    for (const [handler, method, path] of routes) {
      const response = await handler(
        new Request(`${LAN_ORIGIN}${String(path)}`, {
          method,
          headers: {
            Origin: LAN_ORIGIN,
            'Content-Type': 'application/json',
            'Idempotency-Key': id,
            'Last-Event-ID': '27',
          },
          ...(method !== 'GET' ? { body: '{}' } : {}),
        }),
      );
      assert.equal(response.status, 200, path);
    }
    globalThis.fetch = async () => assert.fail('Invalid path forwarded');
    for (const path of [
      `/api/v1/conversations/not-a-uuid`,
      `/api/v1/conversations/${id}/members`,
      `/api/v1/generations/${id}/restart`,
      `/api/v1/generations/${id}%2fevents`,
      `/api/v1/generations/not-a-uuid/regenerate`,
      `/api/v1/generations/${id}/regenerate/extra`,
    ]) {
      assert.equal(
        (await GET(new Request(`${LAN_ORIGIN}${path}`))).status,
        404,
      );
    }
  });

  it('PATCH와 DELETE도 정확한 Origin과 JSON 본문을 요구한다', async () => {
    const path = '/api/v1/conversations/d8402e8f-040a-4385-91cc-238d0c65d355';
    for (const [handler, method] of [
      [PATCH, 'PATCH'],
      [DELETE, 'DELETE'],
    ]) {
      for (const origin of [undefined, 'http://attacker.example']) {
        const response = await handler(
          new Request(`${LAN_ORIGIN}${path}`, {
            method,
            headers: {
              'Content-Type': 'application/json',
              ...(origin ? { Origin: origin } : {}),
            },
            body: '{}',
          }),
        );
        assert.equal(response.status, 403);
      }
      const response = await handler(
        new Request(`${LAN_ORIGIN}${path}`, {
          method,
          headers: { Origin: LAN_ORIGIN, 'Content-Type': 'text/plain' },
          body: '{}',
        }),
      );
      assert.equal(response.status, 415);
    }
  });
});
