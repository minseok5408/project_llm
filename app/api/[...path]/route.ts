export const dynamic = 'force-dynamic';

const API_METHODS: Readonly<Record<string, readonly string[]>> = {
  '/api/status': ['GET'],
  '/api/chat': ['POST'],
  '/api/v1/auth/config': ['GET'],
  '/api/v1/auth/me': ['GET'],
  '/api/v1/auth/login': ['POST'],
  '/api/v1/auth/signup': ['POST'],
  '/api/v1/auth/logout': ['POST'],
  '/api/v1/workspaces': ['GET'],
  '/api/v1/conversations': ['GET', 'POST'],
  '/api/v1/usage': ['GET'],
  '/api/v1/network-mode': ['GET', 'PATCH'],
  '/api/v1/network-mode/check': ['POST'],
};
const UUID =
  '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}';
const CONVERSATION = new RegExp(`^/api/v1/conversations/${UUID}$`);
const MESSAGES = new RegExp(`^/api/v1/conversations/${UUID}/messages$`);
const GENERATION = new RegExp(`^/api/v1/generations/${UUID}(?:/events)?$`);
const GENERATION_ACTION = new RegExp(
  `^/api/v1/generations/${UUID}/(?:cancel|regenerate)$`,
);

function allowedMethods(path: string): readonly string[] | undefined {
  if (CONVERSATION.test(path)) return ['GET', 'PATCH', 'DELETE'];
  if (MESSAGES.test(path)) return ['GET', 'POST'];
  if (GENERATION.test(path)) return ['GET'];
  if (GENERATION_ACTION.test(path)) return ['POST'];
  return API_METHODS[path];
}
const REQUEST_HEADERS = [
  'accept',
  'content-type',
  'cookie',
  'origin',
  'x-csrf-token',
  'idempotency-key',
  'last-event-id',
];
const RESPONSE_HEADERS = ['content-type', 'retry-after', 'x-accel-buffering'];

function apiOrigin() {
  const target = new URL(
    process.env.API_BASE_URL?.trim() || 'http://127.0.0.1:8000',
  );
  if (
    target.protocol !== 'http:' ||
    target.hostname !== '127.0.0.1' ||
    target.username ||
    target.password ||
    target.pathname !== '/' ||
    target.search ||
    target.hash
  ) {
    throw new Error('Invalid API origin');
  }
  return target.origin;
}

async function proxy(request: Request): Promise<Response> {
  const incoming = new URL(request.url);
  const methods = allowedMethods(incoming.pathname);
  const method = request.method;
  const isWrite = !['GET', 'HEAD'].includes(method);
  if (!methods) {
    return Response.json(
      { detail: 'Not found' },
      { status: 404, headers: { 'Cache-Control': 'no-store' } },
    );
  }
  if (!methods.includes(method)) {
    return Response.json(
      { detail: 'Method not allowed' },
      {
        status: 405,
        headers: { Allow: methods.join(', '), 'Cache-Control': 'no-store' },
      },
    );
  }
  if (isWrite && request.headers.get('origin') !== incoming.origin) {
    return Response.json(
      { detail: 'Forbidden origin' },
      { status: 403, headers: { 'Cache-Control': 'no-store' } },
    );
  }
  if (
    isWrite &&
    request.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !==
      'application/json'
  ) {
    return Response.json(
      { detail: 'JSON content type required' },
      { status: 415, headers: { 'Cache-Control': 'no-store' } },
    );
  }

  try {
    // 허용된 API 경로만 고정 loopback 서버로 전달한다.
    const target = new URL(incoming.pathname, apiOrigin());
    target.search = incoming.search;
    const headers = new Headers();
    for (const name of REQUEST_HEADERS) {
      const value = request.headers.get(name);
      if (value !== null) headers.set(name, value);
    }
    // 브라우저가 보낸 전달용 헤더를 신뢰하지 않고 웹 서버의 출처를 기록한다.
    headers.set('X-Project-LLM-Origin', incoming.origin);

    const init: RequestInit & { duplex?: 'half' } = {
      method,
      headers,
      cache: 'no-store',
      redirect: 'error',
      signal: request.signal,
    };
    if (isWrite) {
      init.body = request.body;
      init.duplex = 'half';
    }

    const upstream = await fetch(target, init);
    const responseHeaders = new Headers({ 'Cache-Control': 'no-store' });
    for (const name of RESPONSE_HEADERS) {
      const value = upstream.headers.get(name);
      if (value !== null) responseHeaders.set(name, value);
    }
    if (upstream.headers.get('content-type')?.startsWith('text/event-stream')) {
      responseHeaders.set('Cache-Control', 'no-store, no-transform');
    }
    // 쿠키마다 별도 헤더를 유지해야 로그인과 삭제 쿠키가 올바르게 적용된다.
    for (const cookie of upstream.headers.getSetCookie()) {
      responseHeaders.append('Set-Cookie', cookie);
    }

    // 본문을 읽어 모으지 않아 SSE 청크와 클라이언트 취소가 그대로 전파된다.
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return Response.json(
      { detail: 'API gateway unavailable' },
      { status: 502, headers: { 'Cache-Control': 'no-store' } },
    );
  }
}

export async function GET(request: Request) {
  return proxy(request);
}

export async function POST(request: Request) {
  return proxy(request);
}

export async function PATCH(request: Request) {
  return proxy(request);
}

export async function DELETE(request: Request) {
  return proxy(request);
}
