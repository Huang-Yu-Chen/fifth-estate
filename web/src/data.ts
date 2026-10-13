import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { Meta, WindowKey } from "./types";

const cache = new Map<string, Promise<unknown>>();

export function load<T>(path: string): Promise<T> {
  if (!cache.has(path)) {
    const p = fetch(`${import.meta.env.BASE_URL}data/${path}`).then((r) => {
      if (!r.ok) throw new Error(`${r.status} ${path}`);
      return r.json();
    });
    p.catch(() => cache.delete(path));
    cache.set(path, p);
  }
  return cache.get(path) as Promise<T>;
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
  return useData<Meta>("meta.json");
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
