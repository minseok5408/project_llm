import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { it } from 'node:test';
import { POST } from '../app/api/[...path]/route.ts';

it('실제 Node fetch의 스트림 POST에서 인증 실패를 401로 전달하고 쿠키를 보존한다', async () => {
  const previousOrigin = process.env.API_BASE_URL;
  const calls = [];
  const upstream = createServer(async (request, response) => {
    let body = '';
    for await (const chunk of request) body += chunk;
    calls.push({ headers: request.headers, body });
    response.writeHead(401, {
      'Content-Type': 'application/json',
      'Set-Cookie': 'project_llm_session=; Max-Age=0; HttpOnly; SameSite=Lax',
    });
    response.end(JSON.stringify({ detail: '로그인이 필요합니다.' }));
  });
  try {
    // 외부 연결 없이 수명 짧은 loopback 테스트 공급자에만 접속한다.
    await new Promise((resolve, reject) => {
      upstream.once('error', reject);
      upstream.listen(0, '127.0.0.1', resolve);
    });
    process.env.API_BASE_URL = `http://127.0.0.1:${upstream.address().port}`;
    const response = await POST(
      new Request(
        'http://localhost:3000/api/v1/generations/11111111-1111-4111-8111-111111111111/respond',
        {
          method: 'POST',
          headers: {
            Origin: 'http://localhost:3000',
            'Content-Type': 'application/json',
            Cookie: 'project_llm_session=expired',
            'X-CSRF-Token': 'synthetic-csrf',
          },
          body: JSON.stringify({ answers: ['직접 입력'] }),
        },
      ),
    );
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { detail: '로그인이 필요합니다.' });
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.match(response.headers.getSetCookie()[0], /Max-Age=0/);
    assert.equal(calls.length, 1);
    assert.deepEqual(JSON.parse(calls[0].body), { answers: ['직접 입력'] });
    assert.equal(calls[0].headers.cookie, 'project_llm_session=expired');
    assert.equal(calls[0].headers['x-csrf-token'], 'synthetic-csrf');
  } finally {
    if (previousOrigin === undefined) delete process.env.API_BASE_URL;
    else process.env.API_BASE_URL = previousOrigin;
    upstream.closeAllConnections();
    await new Promise((resolve) => upstream.close(resolve));
  }
});
