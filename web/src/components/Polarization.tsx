import type { Polarization as Pol } from "../types";
import { num } from "../format";

/** Big polarization number with CI and the two axis components. */
export function PolarizationHero({ pol, label = "極化指數" }: { pol: Pol; label?: string }) {
  return (
    <div className="phero">
      <div>
        <div className="note">{label}（0–100）</div>
        <div className="big">
          {pol.P == null ? "—" : num(pol.P, 0)}
          {pol.ci_low != null && pol.ci_high != null && (
            <small>
              90% 信賴區間 {num(pol.ci_low, 0)}–{num(pol.ci_high, 0)}
            </small>
          )}
        </div>
        {!pol.sufficient && <span className="badge warn">樣本不足，數值僅供參考</span>}
      </div>
      <dl className="parts">
        <dt>綠藍軸</dt>
        <dd>{pol.P_lean == null ? "—" : num(pol.P_lean, 0)}</dd>
        <dt>抗中友中軸</dt>
        <dd>{pol.china_axis_used ? num(pol.P_china, 0) : "未納入（涉中報導不足）"}</dd>
        <dt>合格媒體</dt>
        <dd>{pol.n_outlets} 家</dd>
        <dt>已評分報導</dt>
        <dd>{num(pol.n_articles)} 篇</dd>
      </dl>
    </div>
  );
}

/** Horizontal bar 0–100 with CI whiskers (for ranking lists). */
export function PBar({ P, lo, hi }: { P: number | null; lo: number | null; hi: number | null }) {
  return (
    <div className="pbar" aria-hidden="true">
      {P != null && <div className="fill" style={{ width: `${Math.min(100, P)}%` }} />}
      {lo != null && hi != null && <div className="ci" style={{ left: `${lo}%`, width: `${Math.max(0, hi - lo)}%` }} />}
    </div>
  );
}
