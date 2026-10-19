import { Suspense, useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { countPageView, useMeta, useWindowKey } from "../data";
import { shortTime } from "../format";
import type { WindowKey } from "../types";
import { Loading } from "./States";

// The data is updated every 3 hours; data this much older means updates have stopped (e.g. paused while
// the OpenAI quota is used up), and the "last 7 days" are no longer the last 7 days.
const STALE_HOURS = 12;

const NAV = [
  { to: "/", label: "總覽", end: true },
  { to: "/coverage", label: "報導量" },
  { to: "/grid", label: "格狀" },
  { to: "/events", label: "事件" },
  { to: "/methodology", label: "方法論" },
];

export function WindowPicker() {
  const [w, setW] = useWindowKey();
  const opts: [WindowKey, string][] = [
    ["7", "近 7 天"],
    ["30", "30 天"],
    ["90", "90 天"],
  ];
  return (
    <div className="seg" role="group" aria-label="分析期間">
      {opts.map(([k, label]) => (
        <button key={k} type="button" aria-pressed={w === k} onClick={() => setW(k)}>
          {label}
        </button>
      ))}
    </div>
  );
}

export default function Layout() {
  const { data: meta } = useMeta();
  const { search, pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
    countPageView(pathname);
  }, [pathname]);
  const keep = new URLSearchParams(search).get("w");
  const q = keep ? `?w=${keep}` : "";
  const stale = meta && Date.now() - Date.parse(meta.generated_at) > STALE_HOURS * 3_600_000;
  return (
    <>
      {meta?.site?.status === "beta" && !meta.fake_data && (
        <div className="banner" role="status">
          Beta：AI 標註仍在與人工標註比對驗證中，分數僅供參考，請勿當作定論引用。
        </div>
      )}
      {stale && (
        <div className="banner" role="status">
          資料暫停更新：目前顯示的是 {shortTime(meta.generated_at)} 的分析結果。
        </div>
      )}
      {meta?.fake_data && (
        <div className="banner" role="status">
          ⚠ 測試資料：目前的分數是開發用的隨機資料，不代表任何媒體的真實立場。
        </div>
      )}
      <header className="site-header">
        <div className="wrap">
          <NavLink to={`/${q}`} className="brand">
            第五權 <small>THE FIFTH ESTATE</small>
          </NavLink>
          <nav className="main" aria-label="主選單">
            {NAV.map((n) => (
              <NavLink key={n.to} to={`${n.to}${q}`} end={n.end}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <WindowPicker />
        </div>
      </header>
      <main>
        <div className="wrap">
          <Suspense fallback={<Loading />}>
            <Outlet />
          </Suspense>
        </div>
      </main>
      <footer className="site">
        <div className="wrap">
          <span>
            第五權 The Fifth Estate · 方法論改編自 UPenn{" "}
            <a href="https://mediabiasdetector.seas.upenn.edu/" target="_blank" rel="noopener noreferrer">
              Media Bias Detector
            </a>
          </span>
          <span>
            資料更新：{meta ? shortTime(meta.generated_at) : "—"} ·{" "}
            <a href="https://github.com/Huang-Yu-Chen/fifth-estate" target="_blank" rel="noopener noreferrer">
              GitHub
            </a>
          </span>
        </div>
      </footer>
    </>
  );
}
