// Site address and page titles/descriptions, shared by the app (browser tab titles) and the build, which writes
// one HTML file per route with these in <head> for link previews and search engines (vite.config.ts).

export const SITE_URL = "https://the-fifth-estate.org";
export const SITE_NAME = "第五權 The Fifth Estate";
export const SITE_TAGLINE = "台灣新聞媒體的政治極化偵測器";
export const SITE_DESCRIPTION =
  "第五權 The Fifth Estate：台灣新聞媒體的政治極化偵測器。追蹤各家網路媒體的政治、兩岸與外交報導，計算綠藍傾向、對中立場與議題極化指數。";

const COMPARE = "比較各家媒體的報導量、綠藍傾向、抗中友中立場與極化指數。";

/** Fixed pages besides the overview, keyed by path. */
export const PAGES = {
  coverage: {
    title: "報導量與立場分布",
    description: "每家媒體在各議題的報導數，依綠藍傾向、抗中友中立場或語氣分組堆疊（仿 Media Bias Detector 的長條圖）。",
  },
  grid: {
    title: "媒體 × 議題",
    description: "每家媒體在每個議題的報導數與平均立場一覽（仿 Media Bias Detector 的格狀圖）。",
  },
  events: {
    title: "每日事件",
    description: `把內容相似的報導連成同一新聞事件，${COMPARE}`,
  },
  methodology: {
    title: "方法論",
    description: "第五權如何收集新聞、由 AI 標註綠藍傾向與對中立場、計算極化指數，以及和賓州大學 Media Bias Detector 的異同。",
  },
} as const;

/** Tab title; no name means the overview. */
export function pageTitle(name?: string | null): string {
  return name ? `${name}｜第五權` : `${SITE_NAME}｜${SITE_TAGLINE}`;
}

export function unitDescription(kind: string, name: string, detail?: string): string {
  if (kind === "event") return `新聞事件「${name}」：${COMPARE}`;
  return detail ? `${name}：${detail}。${COMPARE}` : `${name}：${COMPARE}`;
}

export function outletDescription(name: string): string {
  return `${name}的政治、兩岸與外交新聞：每週平均綠藍傾向與抗中友中立場、議題分布與最新報導。`;
}
