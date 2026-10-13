import { Link } from "react-router-dom";
import type { Axis, Meta, OutletStats, WindowKey } from "../types";
import { num, pct, signed } from "../format";
import { scoreColor, type Theme } from "../palette";

function DBar({ value, ci, axis, theme }: { value: number | null; ci: number | null; axis: Axis; theme: Theme }) {
  if (value == null) return <span className="note">—</span>;
  const pos = (v: number) => `${((Math.max(-5, Math.min(5, v)) + 5) / 10) * 100}%`;
  const left = Math.min(0, value);
  const right = Math.max(0, value);
  return (
    <span className="dbar">
      <span className="track" aria-hidden="true">
        <span className="mark" style={{ left: pos(left), width: `calc(${pos(right)} - ${pos(left)})`, background: scoreColor(theme, axis, value) }} />
        {ci != null && <span className="ci" style={{ left: pos(value - ci), width: `calc(${pos(value + ci)} - ${pos(value - ci)})` }} />}
      </span>
      <span>{signed(value)}</span>
    </span>
  );
}

function Types({ counts, theme }: { counts: number[]; theme: Theme }) {
  const total = counts.reduce((a, b) => a + b, 0) || 1;
  const colors = [theme.muted, theme.accent, theme.buckets.tone[3]];
  return (
    <span className="types" title={`報導 ${counts[0]}・分析 ${counts[1]}・評論 ${counts[2]}`}>
      {counts.map((c, i) => (
        <span key={i} style={{ width: `${(c / total) * 100}%`, background: colors[i] }} />
      ))}
    </span>
  );
}

export default function OutletTable({
  meta,
  stats,
  theme,
  w,
  showShare = true,
}: {
  meta: Meta;
  stats: Record<string, OutletStats>;
  theme: Theme;
  w: WindowKey;
  showShare?: boolean;
}) {
  const rows = meta.outlets.filter((o) => stats[o.id]);
  const anyYahoo = rows.some((o) => o.via === "yahoo");
  return (
    <>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>媒體</th>
              <th>報導數</th>
              {showShare && <th title="占該媒體同期政治新聞的比例">占比</th>}
              <th>綠藍平均（±95% CI）</th>
              <th title="只計與中國相關的報導">抗中友中平均</th>
              <th>涉中篇數</th>
              <th>語氣</th>
              <th title="新聞報導・新聞分析・評論">類型組成</th>
              <th>轉載中央社</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => {
              const s = stats[o.id];
              return (
                <tr key={o.id}>
                  <td>
                    <Link to={`/outlet/${o.id}?w=${w}`}>{o.short}</Link>
                    {o.via === "yahoo" && <sup title="經 Yahoo 轉載取得">*</sup>}
                  </td>
                  <td>{num(s.n)}</td>
                  {showShare && <td>{pct(s.share, 1)}</td>}
                  <td>
                    <DBar value={s.lean} ci={s.lean_ci} axis="lean" theme={theme} />
                  </td>
                  <td>
                    <DBar value={s.china} ci={s.china_ci} axis="china" theme={theme} />
                  </td>
                  <td>{num(s.china_n)}</td>
                  <td>{signed(s.tone)}</td>
                  <td>
                    <Types counts={s.news_types} theme={theme} />
                  </td>
                  <td>{pct(s.wire_share)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {anyYahoo && <p className="note">* 經 Yahoo 奇摩新聞轉載取得，僅含被轉載的文章，報導數可能偏低。</p>}
    </>
  );
}
