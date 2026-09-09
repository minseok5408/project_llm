'use client';

import { useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import { GraphemeTyper } from './grapheme-typer';

export function StreamingText({
  content,
  active,
  frozen,
  reducedMotion,
  fallback,
}: {
  content: string;
  active: boolean;
  frozen: boolean;
  reducedMotion: boolean;
  fallback: string;
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
  return <>{shown || fallback}</>;
}
