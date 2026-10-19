import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { pageTitle } from "./site";
import type { ArticlePage, Meta, WindowKey } from "./types";

// Data comes from the API (src/fifth_estate/api), not from files in this build. Production builds take its
// address from VITE_API_BASE (.env.production); in development it is empty and Vite proxies /v1 to
// `uv run fifth serve` (vite.config.ts).
export const API = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
// Ask again after this long: the data changes every few hours, and the browser revalidates cheaply (ETag).
const TTL_MS = 5 * 60_000;

const cache = new Map<string, { at: number; p: Promise<unknown> }>();

export function load<T>(path: string): Promise<T> {
  const hit = cache.get(path);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.p as Promise<T>;
  const p = fetch(`${API}/v1/${path}`).then((r) => {
    if (!r.ok) throw new Error(`${r.status} ${path}`);
    return r.json();
  });
  p.catch(() => {
    if (cache.get(path)?.p === p) cache.delete(path);
  });
  cache.set(path, { at: Date.now(), p });
  return p as Promise<T>;
}

/** One page of a unit's (or an outlet's) articles, newest first. */
export function loadArticles(q: { unit?: string; outlet?: string; limit: number; cursor?: string | null }): Promise<ArticlePage> {
  const params = new URLSearchParams({ unit: q.unit ?? "all/all", limit: String(q.limit) });
  if (q.outlet) params.set("outlet", q.outlet);
  if (q.cursor) params.set("cursor", q.cursor);
  return load<ArticlePage>(`articles?${params}`);
}

let referrer = document.referrer; // only the first page of a visit has one

/** Anonymous page-view count (no cookies; see src/fifth_estate/api/hits.py). Never gets in the way. */
export function countPageView(route: string): void {
  const body = JSON.stringify({ route, referrer });
  referrer = "";
  try {
    if (!navigator.sendBeacon?.(`${API}/v1/hit`, body)) {
      fetch(`${API}/v1/hit`, { method: "POST", body, keepalive: true, mode: "no-cors" }).catch(() => {});
    }
  } catch {
    /* counting must never break the page */
  }
}

export interface Loaded<T> {
  data?: T;
  error?: Error;
  loading: boolean;
}

export function useData<T>(path: string | null): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ loading: !!path });
  useEffect(() => {
    if (!path) {
      setState({ loading: false });
      return;
    }
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: undefined }));
    load<T>(path).then(
      (data) => alive && setState({ data, loading: false }),
      (error: Error) => alive && setState({ error, loading: false }),
    );
    return () => {
      alive = false;
    };
  }, [path]);
  return state;
}

export function useMeta(): Loaded<Meta> {
  return useData<Meta>("meta");
}

/** Browser tab title (see pageTitle); null for the overview, undefined while the name is still loading. */
export function usePageTitle(name: string | null | undefined) {
  useEffect(() => {
    if (name !== undefined) document.title = pageTitle(name);
  }, [name]);
}

const WINDOW_KEY = "fifth-estate-window";
const WINDOWS: WindowKey[] = ["7", "30", "90"];

function stored(): WindowKey {
  try {
    const v = localStorage.getItem(WINDOW_KEY);
    if (v && (WINDOWS as string[]).includes(v)) return v as WindowKey;
  } catch {
    /* storage unavailable */
  }
  return "7";
}

/** Analysis window in days, kept in the URL (?w=30) so links are shareable. */
export function useWindowKey(): [WindowKey, (w: WindowKey) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("w");
  const w = raw && (WINDOWS as string[]).includes(raw) ? (raw as WindowKey) : stored();
  const set = (next: WindowKey) => {
    try {
      localStorage.setItem(WINDOW_KEY, next);
    } catch {
      /* storage unavailable */
    }
    const p = new URLSearchParams(params);
    p.set("w", next);
    setParams(p, { replace: true });
  };
  return [w, set];
}
