const langfuseHost = process.env.NEXT_PUBLIC_LANGFUSE_URL ?? "http://localhost:3001";

export function traceUrl(traceId: string): string {
  return `${langfuseHost.replace(/\/$/, "")}/trace/${encodeURIComponent(traceId)}`;
}
