export type RequestFn = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

// 인증된 요청 함수를 주입받는 JSON 호출의 공통 계약이다.
export type JsonRequest = <T>(path: string, init?: RequestInit) => Promise<T>;

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function responseError(response: Response, fallback: string) {
  const data: unknown = await response.json().catch(() => null);
  if (
    data &&
    typeof data === 'object' &&
    'detail' in data &&
    typeof data.detail === 'string'
  ) {
    return data.detail;
  }
  return fallback;
}
