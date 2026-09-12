'use client';

import { useId, useState } from 'react';

export function isLongUserMessage(content: string): boolean {
  return content.length > 600 || content.split('\n').length > 8;
}

/** 접어도 원문을 자르지 않아 복사·검색·접근성에서 같은 내용을 유지한다. */
export function UserMessageText({
  content,
  revealKey,
}: {
  content: string;
  revealKey?: string | number;
}) {
  const id = useId();
  const [view, setView] = useState({
    expanded: revealKey !== undefined,
    revealKey,
  });
  const revealed = revealKey !== undefined && view.revealKey !== revealKey;
  const expanded = view.expanded || revealed;
  if (revealed) setView({ expanded: true, revealKey });
  if (!isLongUserMessage(content)) return <>{content}</>;
  return (
    <>
      <div id={id} className={expanded ? '' : 'line-clamp-8'}>
        {content}
      </div>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={id}
        onClick={() => setView({ expanded: !expanded, revealKey })}
        className="mt-2 min-h-11 rounded-lg px-2 text-xs text-muted-foreground hover:bg-background/50 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
      >
        {expanded ? '접기' : '더 보기'}
      </button>
    </>
  );
}
