'use client';

import { useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import { GraphemeTyper } from '../stream/grapheme-typer';
import { MarkdownText } from '../../../components/content/markdown-text';

export function StreamingText({
  content,
  active,
  frozen,
  reducedMotion,
  fallback,
  markdown = false,
}: {
  content: string;
  active: boolean;
  frozen: boolean;
  reducedMotion: boolean;
  fallback: string;
  markdown?: boolean;
}) {
  const [typer] = useState(() => new GraphemeTyper(active ? '' : content));
  const wasFrozen = useRef(false);
  const shown = useSyncExternalStore(
    typer.subscribe,
    typer.getSnapshot,
    typer.getSnapshot,
  );

  useLayoutEffect(() => {
    if (active && frozen) {
      wasFrozen.current = true;
      typer.freeze();
    } else if (active) {
      wasFrozen.current = false;
      typer.resume(content);
      if (reducedMotion) typer.flushStable();
    } else if (!wasFrozen.current) {
      typer.resume(content);
      typer.complete(content);
    }
  }, [active, content, frozen, reducedMotion, typer]);

  useLayoutEffect(() => () => typer.dispose(), [typer]);
  if (!shown) return <span>{fallback}</span>;
  return markdown ? <MarkdownText content={shown} /> : <>{shown}</>;
}
