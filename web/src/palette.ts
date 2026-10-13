import { useEffect, useState } from "react";
import type { Axis } from "./types";

// Five MBD buckets per axis: [−5,−4] [−3,−2] [−1,1] [2,3] [4,5].
// 綠藍 keeps party colours (green ↔ blue); 抗中友中 uses teal ↔ orange so it never reads as a party axis;
// tone uses indigo ↔ amber. Neutral is the same warm grey everywhere.
const PALETTES: Record<Axis, { light: string[]; dark: string[] }> = {
  lean: {
    light: ["#11744a", "#7fbf98", "#b4b2a9", "#8db0e3", "#1c56b4"],
    dark: ["#3bbd78", "#2f7550", "#6f6d66", "#3a5f96", "#6b9df0"],
  },
  china: {
    light: ["#0b6f75", "#7fc0c3", "#b4b2a9", "#efb27c", "#bb560c"],
    dark: ["#33b8c0", "#2b6e72", "#6f6d66", "#8d5a2c", "#f39445"],
  },
  tone: {
    light: ["#4a3d8f", "#a69cd3", "#b4b2a9", "#e9c46a", "#a87508"],
    dark: ["#a094ee", "#4c4580", "#6f6d66", "#7f6626", "#f2c548"],
  },
};

export interface Theme {
  dark: boolean;
  text: string;
  muted: string;
  line: string;
  surface: string;
  accent: string;
  buckets: Record<Axis, string[]>;
}

function read(dark: boolean): Theme {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return {
    dark,
    text: v("--text"),
    muted: v("--muted"),
    line: v("--line"),
    surface: v("--surface"),
    accent: v("--accent"),
    buckets: {
      lean: PALETTES.lean[dark ? "dark" : "light"],
      china: PALETTES.china[dark ? "dark" : "light"],
      tone: PALETTES.tone[dark ? "dark" : "light"],
    },
  };
}

const mq = () => window.matchMedia("(prefers-color-scheme: dark)");

export function useTheme(): Theme {
  const [theme, setTheme] = useState<Theme>(() => read(mq().matches));
  useEffect(() => {
    const m = mq();
    const on = () => setTheme(read(m.matches));
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return theme;
}

export function bucketIndex(score: number): number {
  if (score <= -4) return 0;
  if (score <= -2) return 1;
  if (score <= 1) return 2;
  if (score <= 3) return 3;
  return 4;
}

export function scoreColor(theme: Theme, axis: Axis, score: number | null | undefined): string {
  if (score == null) return theme.line;
  return theme.buckets[axis][bucketIndex(Math.round(score))];
}

/** Continuous diverging colour for heatmaps (−5…+5). */
export function divergingStops(theme: Theme, axis: Axis): string[] {
  return theme.buckets[axis];
}
