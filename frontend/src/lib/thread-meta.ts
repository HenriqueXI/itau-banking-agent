// Client-side conversation titles: the backend only exposes thread ids.

const KEY = "itau-thread-meta";

export interface ThreadMeta {
  title: string;
  updatedAt: number;
}

export function loadThreadMeta(): Record<string, ThreadMeta> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "{}") as Record<string, ThreadMeta>;
  } catch {
    return {};
  }
}

export function saveThreadTitle(threadId: string, title: string): void {
  const meta = loadThreadMeta();
  if (!meta[threadId]) meta[threadId] = { title: title.slice(0, 42), updatedAt: Date.now() };
  else meta[threadId].updatedAt = Date.now();
  localStorage.setItem(KEY, JSON.stringify(meta));
}
