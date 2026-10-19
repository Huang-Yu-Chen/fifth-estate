// Mirrors the JSON served by the API (src/fifth_estate/api/app.py), built by src/fifth_estate/export/site_data.py

export type WindowKey = "7" | "30" | "90";
export type UnitKind = "all" | "category" | "topic" | "subtopic" | "event";
export type Axis = "lean" | "china" | "tone";

export interface OutletMeta {
  id: string;
  name: string;
  short: string;
  via: string | null;
  baseline_lean: number | null;
  baseline_china: number | null;
  n: Record<string, number>;
}

export interface Topic {
  id: string;
  name: string;
  category: string;
  description: string;
}

export interface Subtopic {
  id: string;
  topic: string;
  name: string;
  desc: string;
  origin: string;
}

export interface Meta {
  generated_at: string;
  fake_data: boolean;
  site?: { status?: string };
  windows: number[];
  outlets: OutletMeta[];
  categories: Record<string, string>;
  topics: Topic[];
  subtopics: Subtopic[];
  news_types: string[];
  buckets: { lean: string[]; china: string[]; tone: string[] };
  models: Record<string, { model: string; reasoning_effort: string | null }>;
  params: { polarization: Record<string, number>; events: Record<string, number> };
  counts: Record<string, number>;
}

export interface Polarization {
  P: number | null;
  P_lean: number | null;
  P_china: number | null;
  ci_low: number | null;
  ci_high: number | null;
  china_axis_used: boolean;
  n_outlets: number;
  n_outlets_china: number;
  n_articles: number;
  sufficient: boolean;
}

export interface OutletStats {
  n: number;
  share: number | null;
  n_scored: number;
  lean: number | null;
  lean_ci: number | null;
  lean_buckets: number[];
  title_lean: number | null;
  china_n: number;
  china: number | null;
  china_ci: number | null;
  china_buckets: number[];
  tone: number | null;
  tone_buckets: number[];
  news_types: number[];
  wire_share: number;
}

export interface WindowMetrics {
  n: number;
  n_scored: number;
  polarization: Polarization;
  attention_skew: number | null;
  no_opinion?: Polarization | null;
  outlets: Record<string, OutletStats>;
}

export interface Article {
  id: string;
  outlet: string;
  title: string;
  url: string;
  published: string | null;
  type: string | null;
  tone: number | null;
  lean: number | null;
  title_lean: number | null;
  china: number | null;
  china_rel: number | null;
  lean_reason: string | null;
  china_reason: string | null;
  takeaways: string[];
  topic: string | null;
  subtopic: string | null;
  event: string | null;
  wire: boolean;
  via: string | null;
}

export interface TrendPoint {
  date: string;
  n: number;
  P: number | null;
  P_lean?: number | null;
  P_china?: number | null;
}

export interface UnitDoc {
  kind: UnitKind;
  id: string;
  name: string;
  description?: string;
  category?: string;
  topic?: string;
  topic_name?: string;
  subtopic?: string | null;
  short_title?: string;
  first?: string;
  last?: string;
  window_end?: string;
  origin?: string;
  windows: Record<string, WindowMetrics>;
  trend?: TrendPoint[];
}

export interface IndexWindow {
  n: number;
  n_scored: number;
  P: number | null;
  P_lean: number | null;
  P_china: number | null;
  ci_low: number | null;
  ci_high: number | null;
  sufficient: boolean;
  n_outlets: number;
  china_axis_used: boolean;
  attention_skew: number | null;
  outlets: Record<string, number>;
}

export interface IndexEntry {
  id: string;
  name: string;
  category?: string;
  topic?: string;
  topic_name?: string;
  short_title?: string;
  first?: string;
  last?: string;
  origin?: string;
  windows: Record<string, IndexWindow>;
}

export type IndexDoc = Partial<Record<UnitKind, IndexEntry[]>>;

export type GridCell = { n: number; lean: number | null; china: number | null; tone: number | null };
export type GridDoc = Record<string, Record<string, Record<string, GridCell>>>;

export interface OutletDoc {
  id: string;
  name: string;
  short: string;
  via: string | null;
  baseline_lean: number | null;
  baseline_china: number | null;
  weekly: { week_end: string; n: number; lean: number | null; china: number | null }[];
  topic_mix_30d: Record<string, number>;
}

/** GET /v1/articles: one page, newest first. The first page also counts the whole unit (all outlets). */
export interface ArticlePage {
  articles: Article[];
  next: string | null;
  total?: number;
  outlets?: Record<string, number>;
}
