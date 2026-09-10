import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";

const API_FALLBACK = "http://localhost:8000/api/v1";

function makeLocalStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key) => store.get(key) ?? null,
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => {
      store.delete(key);
    },
    setItem: (key, value) => {
      store.set(key, String(value));
    },
  } as Storage;
}

async function loadApi(baseUrl?: string) {
  vi.resetModules();
  if (baseUrl === undefined) {
    delete process.env.NEXT_PUBLIC_API_URL;
  } else {
    process.env.NEXT_PUBLIC_API_URL = baseUrl;
  }
  const mod = await import("./api");
  return mod.api;
}

function okResponse(config: InternalAxiosRequestConfig): AxiosResponse {
  return {
    data: {},
    status: 200,
    statusText: "OK",
    headers: {},
    config,
  };
}

describe("api client (smoke)", () => {
  let store: Storage;
  let location: { href: string };

  beforeEach(() => {
    store = makeLocalStorage();
    location = { href: "http://app.local/dashboard" };
    vi.stubGlobal("window", { localStorage: store, location });
    vi.stubGlobal("localStorage", store);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.NEXT_PUBLIC_API_URL;
  });

  it("falls back to the default API base URL when unset", async () => {
    const api = await loadApi();
    expect(api.defaults.baseURL).toBe(API_FALLBACK);
  });

  it("honors NEXT_PUBLIC_API_URL when configured", async () => {
    const api = await loadApi("https://api.example.io/api/v1");
    expect(api.defaults.baseURL).toBe("https://api.example.io/api/v1");
  });

  it("attaches the stored token as a bearer header", async () => {
    const api = await loadApi();
    store.setItem("oracle_token", "token-123");

    let captured: string | undefined;
    api.defaults.adapter = async (config) => {
      captured = config.headers.get("Authorization") as string | undefined;
      return okResponse(config);
    };

    await api.get("/campaigns/x/status");
    expect(captured).toBe("Bearer token-123");
  });

  it("does not attach a header when no token is stored", async () => {
    const api = await loadApi();

    let captured: string | undefined;
    api.defaults.adapter = async (config) => {
      captured = config.headers.get("Authorization") as string | undefined;
      return okResponse(config);
    };

    await api.get("/campaigns/x/status");
    expect(captured).toBeUndefined();
  });

  it("clears auth and redirects to /login on a 401", async () => {
    const api = await loadApi();
    store.setItem("oracle_token", "token-123");
    store.setItem("oracle_user", '{"id":"u1"}');

    api.defaults.adapter = async () => {
      const error: unknown = Object.assign(new Error("Unauthorized"), {
        response: { status: 401 },
      });
      throw error;
    };

    await expect(api.get("/campaigns/x/status")).rejects.toThrow("Unauthorized");

    expect(store.getItem("oracle_token")).toBeNull();
    expect(store.getItem("oracle_user")).toBeNull();
    expect(location.href).toBe("/login");
  });
});