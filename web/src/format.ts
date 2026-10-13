import type { Axis, Meta, UnitKind } from "./types";

export const AXIS_LABEL: Record<Axis, string> = { lean: "綠藍", china: "抗中友中", tone: "語氣" };
export const AXIS_ENDS: Record<Axis, [string, string]> = {
  lean: ["親綠", "親藍"],
  china: ["抗中", "友中"],
  tone: ["負面", "正面"],
};
export const KIND_LABEL: Record<UnitKind, string> = {
  all: "全部",
  category: "類別",
  topic: "議題",
  subtopic: "子議題",
  event: "事件",
};

const MINUS = "−";

export function signed(x: number | null | undefined, digits = 1): string {
  if (x == null || Number.isNaN(x)) return "—";
  const s = Math.abs(x).toFixed(digits);
  if (Number(s) === 0) return (0).toFixed(digits);
  return (x > 0 ? "+" : MINUS) + s;
}

export function num(x: number | null | undefined, digits = 0): string {
  if (x == null || Number.isNaN(x)) return "—";
  return x.toLocaleString("zh-TW", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function pct(x: number | null | undefined, digits = 0): string {
  if (x == null || Number.isNaN(x)) return "—";
  return `${(x * 100).toFixed(digits)}%`;
}

/** "10/13 08:15" in Taipei time (timestamps are exported with +08:00). */
export function shortTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = iso.match(/^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  return m ? `${Number(m[1])}/${Number(m[2])} ${m[3]}:${m[4]}` : iso;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${Number(m[2])}/${Number(m[3])}` : iso;
}

export function outletName(meta: Meta | undefined, id: string): string {
  return meta?.outlets.find((o) => o.id === id)?.short ?? id;
}

export function topicName(meta: Meta | undefined, id: string | null | undefined): string {
  if (!id) return "";
  return meta?.topics.find((t) => t.id === id)?.name ?? meta?.subtopics.find((s) => s.id === id)?.name ?? id;
}

export function skewSentence(skew: number | null | undefined): string {
  if (skew == null) return "資料不足以判斷各陣營媒體的關注程度。";
  if (skew >= 0.3) return `這個議題的報導主要來自偏藍的媒體（選題偏向 ${signed(skew)}）。`;
  if (skew <= -0.3) return `這個議題的報導主要來自偏綠的媒體（選題偏向 ${signed(skew)}）。`;
  return `各陣營媒體對這個議題的關注程度相近（選題偏向 ${signed(skew)}）。`;
}
