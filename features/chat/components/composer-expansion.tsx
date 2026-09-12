'use client';

import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';
import { Maximize2, Minimize2 } from 'lucide-react';

const Expansion = createContext({ expanded: false, toggle: () => {} });

/** 입력 요소를 교체하지 않고 화면 안에서 편집 공간만 넓힌다. */
export function ComposerExpansion({ children }: { children: ReactNode }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <Expansion.Provider
      value={{ expanded, toggle: () => setExpanded((current) => !current) }}
    >
      <div
        data-expanded={expanded}
        className={`group/composer ${expanded ? 'max-h-[calc(100dvh-9rem)] overflow-y-auto rounded-[28px]' : ''}`}
      >
        {children}
      </div>
    </Expansion.Provider>
  );
}

export function ComposerExpansionToggle() {
  const { expanded, toggle } = useContext(Expansion);
  const label = expanded ? '입력창 축소' : '입력창 확대';
  const Icon = expanded ? Minimize2 : Maximize2;
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      title={label}
      aria-expanded={expanded}
      aria-controls="chat-message-input"
      className="inline-flex size-11 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
    >
      <Icon className="size-4" aria-hidden="true" />
    </button>
  );
}
