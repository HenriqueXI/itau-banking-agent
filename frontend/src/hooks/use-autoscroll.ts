"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Keeps a scroll container pinned to the bottom while new content streams in. */
export function useAutoscroll(dependency: unknown) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [pinned, setPinned] = useState(true);

  const onScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 48);
  }, []);

  const jumpToLatest = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    setPinned(true);
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (el && pinned) el.scrollTop = el.scrollHeight;
  }, [dependency, pinned]);

  return { containerRef, onScroll, pinned, jumpToLatest };
}
