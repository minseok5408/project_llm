'use client';

import { useEffect, useState } from 'react';

/** 운영체제의 모션 설정 변경을 구독하고 화면 종료 때 구독을 해제한다. */
export function useReducedMotion() {
  const [reducedMotion, setReducedMotion] = useState(false);
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const sync = () => setReducedMotion(query.matches);
    sync();
    query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, []);
  return reducedMotion;
}
