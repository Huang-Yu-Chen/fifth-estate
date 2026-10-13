import { Link } from "react-router-dom";
import type { IndexEntry, UnitKind, WindowKey } from "../types";
import { num } from "../format";
import { PBar } from "./Polarization";
import { Empty } from "./States";

interface Props {
  kind: UnitKind;
  entries: IndexEntry[];
  w: WindowKey;
  limit?: number;
  winKey?: string;
}

export default function RankingList({ kind, entries, w, limit = 12, winKey }: Props) {
  const key = winKey ?? w;
  const rows = entries
    .filter((e) => e.windows[key] && e.windows[key].n > 0)
    .sort((a, b) => {
      const A = a.windows[key];
      const B = b.windows[key];
      if (A.sufficient !== B.sufficient) return A.sufficient ? -1 : 1;
      return (B.P ?? -1) - (A.P ?? -1);
    })
    .slice(0, limit);
  if (!rows.length) return <Empty>這段期間還沒有足夠的報導。</Empty>;
  return (
    <ul className="rank">
      {rows.map((e) => {
        const m = e.windows[key];
        return (
          <li key={e.id} className={m.sufficient ? "" : "insufficient"} title={m.sufficient ? undefined : "樣本不足"}>
            <span className="name">
              <Link to={`/unit/${kind}/${e.id}?w=${w}`}>{e.name}</Link>
              <small>{num(m.n)} 篇</small>
            </span>
            <PBar P={m.P} lo={m.ci_low} hi={m.ci_high} />
            <span className="value">{m.P == null ? "—" : num(m.P, 0)}</span>
          </li>
        );
      })}
    </ul>
  );
}
