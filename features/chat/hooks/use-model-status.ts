'use client';

import { useEffect, useState } from 'react';
import type { RequestFn } from '../../../lib/http.ts';

const INITIAL_RUNTIME = {
  ready: false,
  online: false,
  backend: '확인 중',
  model: 'mlx-community/Qwen3.8-27B-4bit',
  detail: '실행 상태 확인 중',
};
export type ModelRuntime = typeof INITIAL_RUNTIME;

/** 상태 확인 요청과 타이머를 같은 수명으로 정리하고 늦은 응답을 버린다. */
export function watchModelStatus(
  request: RequestFn,
  publish: (update: (previous: ModelRuntime) => ModelRuntime) => void,
) {
  const controller = new AbortController();
  const refresh = async () => {
    try {
      const response = await request('/api/status', {
        signal: controller.signal,
      });
      if (!response.ok) throw new Error('모델 상태를 확인하지 못했습니다.');
      const data = (await response.json()) as {
        provider?: {
          ready?: boolean;
          backend?: string;
          model?: string;
          detail?: string;
        };
      };
      if (!controller.signal.aborted)
        publish(() => ({
          ready: Boolean(data.provider?.ready),
          online: true,
          backend: data.provider?.backend?.toUpperCase() ?? '확인 중',
          model: data.provider?.model ?? INITIAL_RUNTIME.model,
          detail: data.provider?.detail ?? '추론 서버 준비 중',
        }));
    } catch {
      if (!controller.signal.aborted)
        publish((previous) => ({
          ...previous,
          ready: false,
          online: false,
          detail: 'API 연결을 확인해 주세요.',
        }));
    }
  };
  void refresh();
  const timer = setInterval(() => void refresh(), 10_000);
  return () => {
    controller.abort();
    clearInterval(timer);
  };
}

export function useModelStatus(request: RequestFn) {
  const [runtime, setRuntime] = useState(INITIAL_RUNTIME);
  useEffect(() => watchModelStatus(request, setRuntime), [request]);
  return runtime;
}
