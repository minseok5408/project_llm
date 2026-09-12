'use client';

import { useId } from 'react';
import type { QuestionCard as Card } from '../state/interaction-types.ts';

export function QuestionCard({
  card,
  answers,
  available,
  canSend,
  onChange,
  onSubmit,
}: {
  card: Card;
  answers: string[];
  available: boolean;
  canSend: boolean;
  onChange: (index: number, value: string) => void;
  onSubmit: () => void;
}) {
  const id = useId();
  const responded = Boolean(card.response_generation_id);
  return (
    <form
      aria-label="확인 질문"
      onSubmit={(event) => {
        event.preventDefault();
        if (available && canSend) onSubmit();
      }}
      className="mt-3 max-w-xl rounded-2xl border border-border bg-muted/20 p-4 sm:p-5"
    >
      <p className="mb-4 text-sm font-medium">
        {responded
          ? '답변한 확인 질문'
          : available
            ? '답변을 이어가기 위해 알려주세요'
            : '지난 확인 질문'}
      </p>
      <div className="space-y-5">
        {card.questions.map((item, index) => (
          <fieldset key={index} className="min-w-0">
            <legend className="mb-2 w-full break-words text-sm leading-6">
              {index + 1}. {item.question}
            </legend>
            {responded ? (
              <p className="whitespace-pre-wrap break-words rounded-lg bg-muted px-3 py-2 text-sm">
                {card.answers?.[index]}
              </p>
            ) : available ? (
              <>
                {item.options.length > 0 && (
                  <div className="mb-2 flex flex-wrap gap-2">
                    {item.options.map((option) => (
                      <button
                        key={option}
                        type="button"
                        aria-pressed={answers[index] === option}
                        onClick={() => onChange(index, option)}
                        className="max-w-full break-words rounded-xl border border-border px-3 py-2 text-left text-sm leading-5 transition-colors hover:bg-muted aria-pressed:border-foreground/60 aria-pressed:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                )}
                <label htmlFor={`${id}-${index}`} className="sr-only">
                  {item.question} 직접 입력
                </label>
                <textarea
                  id={`${id}-${index}`}
                  rows={2}
                  maxLength={2000}
                  required
                  value={answers[index] ?? ''}
                  onChange={(event) => onChange(index, event.target.value)}
                  placeholder="직접 입력하거나 선택지를 고르세요"
                  className="w-full resize-y rounded-xl border border-input bg-background px-3 py-2 text-sm leading-6 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </>
            ) : null}
          </fieldset>
        ))}
      </div>
      {available && !responded && (
        <button
          type="submit"
          disabled={
            !canSend ||
            card.questions.some((_, index) => !answers[index]?.trim())
          }
          className="mt-4 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-40"
        >
          답변 보내고 계속하기
        </button>
      )}
      {!available && !responded && (
        <p className="mt-3 text-xs leading-5 text-muted-foreground">
          새 메시지로 대화를 이어갈 수 있습니다.
        </p>
      )}
    </form>
  );
}
