import { NextRequest } from "next/server";

const backendUrl = process.env.BACKEND_URL ?? "http://backend:8000";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const url = new URL(`/api/${path.join("/")}`, backendUrl);
  url.search = request.nextUrl.search;
  const headers = new Headers();
  for (const name of ["authorization", "content-type", "accept"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const body = ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer();
  const response = await fetch(url, { method: request.method, headers, body, cache: "no-store" });
  const outgoing = new Headers(response.headers);
  outgoing.delete("content-encoding");
  return new Response(response.body, { status: response.status, headers: outgoing });
}

export const GET = proxy;
export const POST = proxy;
