import { describe, expect, it } from "vitest";
import { _setFetch, api, setAccessToken } from "./api";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("api client", () => {
  it("sends Idempotency-Key and refreshes once on 401", async () => {
    const calls: { url: string; headers: Record<string, string> }[] = [];
    let first = true;
    _setFetch(async (url, init) => {
      calls.push({ url: String(url), headers: (init?.headers ?? {}) as Record<string, string> });
      if (String(url).endsWith("/auth/refresh")) return json(200, { access_token: "new" });
      if (first) { first = false; return json(401, { code: "unauthorized" }); }
      return json(201, { id: "s1" });
    });
    setAccessToken("old");
    const out = await api<{ id: string }>("/api/v1/sessions", { method: "POST", body: {}, idempotencyKey: "k1" });
    expect(out.id).toBe("s1");
    expect(calls.map((c) => c.url)).toEqual(["/api/v1/sessions", "/api/v1/auth/refresh", "/api/v1/sessions"]);
    expect(calls[0].headers["idempotency-key"]).toBe("k1");
    expect(calls[2].headers["idempotency-key"]).toBe("k1"); // same key on retry
    expect(calls[2].headers.authorization).toBe("Bearer new");
  });

  it("maps problem+json to ApiError code", async () => {
    _setFetch(async () => json(403, { code: "consent_required", detail: "x" }));
    await expect(api("/api/v1/x")).rejects.toMatchObject({ status: 403, code: "consent_required" });
  });
});

it("concurrent refreshes share one request (rotating refresh tokens)", async () => {
  const { _setFetch, tryRestore } = await import("./api");
  let n = 0;
  _setFetch(async () => { n++; await new Promise((r) => setTimeout(r, 10));
    return new Response(JSON.stringify({ access_token: "t" }), { status: 200, headers: { "content-type": "application/json" } }); });
  const [a, b] = await Promise.all([tryRestore(), tryRestore()]);
  expect(a && b).toBe(true);
  expect(n).toBe(1);
});
