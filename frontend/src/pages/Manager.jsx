import { useEffect, useState } from 'react';
import { api, inr } from '../api.js';
import { useSession } from '../session.jsx';
import { Bars, Empty, ErrorNote, Layout, Loading } from '../ui.jsx';

const PERIODS = [7, 30, 90];
const pct = (v) => (v === null || v === undefined ? '—' : `${v}%`);
const daysText = (v) => (v === null || v === undefined ? '—' : `${v} d`);

export default function Manager() {
  const { session } = useSession();
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = async () => {
    setError(null);
    try { setData(await api(`/manager/dashboard?days=${days}`, { token: session.token })); }
    catch (err) { setError(err); }
  };
  useEffect(() => { load(); }, [days]); // eslint-disable-line react-hooks/exhaustive-deps

  const t = data?.tiles;
  const tiles = t && [
    ['Return rate', pct(t.return_rate)],
    ['Average cycle time', daysText(t.avg_cycle_days)],
    ['Refund success rate', pct(t.refund_success_rate)],
    ['Waiting for finance', t.waiting_for_finance],
  ];

  return (
    <Layout width="wide" menu={<span aria-current="page" style={{ fontWeight: 600, borderBottom: '2px solid var(--rr-color-primary)', padding: '10px 0' }}>Dashboard</span>}>
      <div className="row between">
        <h1>Returns dashboard</h1>
        <fieldset className="segmented">
          <legend>Period</legend>
          {PERIODS.map((p) => (
            <label key={p}>
              <input type="radio" name="period" checked={days === p} onChange={() => setDays(p)} />
              <span>{p} d</span>
            </label>
          ))}
        </fieldset>
      </div>
      <ErrorNote error={error} onRetry={load} />
      {!data && !error && <Loading label="Loading the dashboard…" />}
      {data && (
        <>
          <div className="row" style={{ flexWrap: 'wrap', alignItems: 'stretch' }} aria-label="Key figures" role="group">
            {tiles.map(([label, value]) => (
              <div key={label} className="tile" style={{ flex: '1 1 180px' }}>
                <span className="muted small">{label}</span>
                <span className="value">{value}</span>
              </div>
            ))}
          </div>

          <div className="grid-panels" style={{ alignItems: 'start' }}>
            <section className="card stack" aria-labelledby="h-cat">
              <h2 id="h-cat" style={{ fontSize: 18 }}>Return rate by category</h2>
              <Bars label="Return rate by category" max={100}
                rows={data.by_category.map((c) => ({ name: c.label, value: c.rate || 0,
                  text: c.rate === null ? '—' : `${c.rate}% (${c.returned}/${c.sold})` }))} />
            </section>
            <section className="card stack" aria-labelledby="h-why">
              <h2 id="h-why" style={{ fontSize: 18 }}>Top reasons</h2>
              {data.top_reasons.length === 0 ? <Empty title="No returns in this period" /> : (
                <Bars label="Top return reasons" max={100}
                  rows={data.top_reasons.map((r) => ({ name: r.label, value: r.share, text: `${r.share}% (${r.count})` }))} />
              )}
            </section>
          </div>

          <section className="card stack" aria-labelledby="h-stage">
            <h2 id="h-stage" style={{ fontSize: 18 }}>Average time in each step</h2>
            <Bars label="Average days in each step"
              rows={data.stages.map((s) => ({ name: s.stage, value: s.avg_days || 0, text: daysText(s.avg_days) }))} />
          </section>

          <section className="card stack" aria-labelledby="h-flag">
            <h2 id="h-flag" style={{ fontSize: 18 }}>Flagged customers</h2>
            <p className="muted small">More than 3 returns in the last 30 days. For information only; it never blocks a return.</p>
            {data.flagged.length === 0 ? <Empty title="No flagged customers" /> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Customer</th><th className="num">Returns (30 d)</th><th className="num">Refunded (30 d)</th></tr></thead>
                  <tbody>
                    {data.flagged.map((f) => (
                      <tr key={f.customer_id}><td>{f.name}</td><td className="num">{f.returns_30d}</td><td className="num">{inr(f.refunded_30d)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </Layout>
  );
}
