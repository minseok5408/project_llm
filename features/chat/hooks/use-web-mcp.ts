'use client';

import { useEffect, useRef } from 'react';

type SendMessage = (text: string) => Promise<boolean>;
export type WebMcpContext = {
  registerTool: (
    tool: {
      name: string;
      title: string;
      description: string;
      inputSchema: Record<string, unknown>;
      annotations: { readOnlyHint: boolean; untrustedContentHint: boolean };
      execute: (input: unknown) => Promise<Record<string, unknown>>;
    },
    options?: { signal?: AbortSignal },
  ) => void | Promise<void>;
};

/** 브라우저 도구는 화면과 같은 전송 검사를 거치고 계정 화면 종료 시 해제한다. */
export function registerChatTool(
  context: WebMcpContext | undefined,
  send: SendMessage,
) {
  const lifecycle = new AbortController();
  if (context?.registerTool) {
    try {
      const result = context.registerTool(
        {
          name: 'send_chat_message',
          title: '채팅 메시지 보내기',
          description:
            '현재 로그인한 계정의 대화에 메시지를 저장하고 응답 생성을 요청합니다.',
          inputSchema: {
            type: 'object',
            properties: {
              message: { type: 'string', minLength: 1, maxLength: 100000 },
            },
            required: ['message'],
            additionalProperties: false,
          },
          annotations: { readOnlyHint: false, untrustedContentHint: true },
          async execute(input) {
            const message =
              typeof input === 'object' && input !== null && 'message' in input
                ? (input as { message?: unknown }).message
                : undefined;
            if (
              typeof message !== 'string' ||
              !message.trim() ||
              message.length > 100000
            )
              throw new Error('메시지는 1~100,000자로 입력해야 합니다.');
            if (lifecycle.signal.aborted || !(await send(message)))
              throw new Error(
                '현재 메시지를 전송할 수 없습니다. 화면의 상태를 확인해 주세요.',
              );
            return { status: 'accepted' };
          },
        },
        { signal: lifecycle.signal },
      );
      void Promise.resolve(result).catch(() => undefined);
    } catch {
      // WebMCP 지원 여부와 관계없이 화면의 채팅은 계속 사용할 수 있다.
    }
  }
  return () => lifecycle.abort();
}

export function useWebMcp(send: SendMessage) {
  const sendRef = useRef(send);
  useEffect(() => {
    sendRef.current = send;
  });
  useEffect(
    () =>
      registerChatTool(
        (document as Document & { modelContext?: WebMcpContext }).modelContext,
        (text) => sendRef.current(text),
      ),
    [],
  );
}
