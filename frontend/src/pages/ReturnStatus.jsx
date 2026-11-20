import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { api, inr, when } from '../api.js';
import { useSession } from '../session.jsx';
import { ErrorNote, Layout, Loading, StateChip, Timeline, useFocusHeading } from '../ui.jsx';

/** Label + per-item status timeline. Used as wizard step 7 and as /returns/:id. */
export default function ReturnStatus({ initial, headingRef }) {
  const { session } = useSession();
  const [view, setView] = useState(initial || null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const { returnId } = useParams();
  const id = initial?.return_id || returnId;

  const refresh = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    setError(null);
    try { setView(await api(`/returns/${encodeURIComponent(id)}`, { token: session.token })); }
    catch (err) { setError(err); }
    finally { setBusy(false); }
  }, [id, session]);

  useEffect(() => { if (!initial) refresh(); }, [initial, refresh]);

  async function cancel(item) {
    setBusy(true);
    setError(null);
    try {
      setView(await api(`/returns/${id}/items/${item.item_id}/cancel`, { method: 'POST', token: session.token }));
    } catch (err) { setError(err); }
    finally { setBusy(false); }
  }

  if (!view) {
    return (
      <div className="card">
        {busy ? <Loading label="Loading your return…" /> : <ErrorNote error={error} onRetry={refresh} />}
      </div>
    );
  }

  const approvedAny = view.items.some((i) => i.state !== 'rejected' || !['NON_RETURNABLE_CATEGORY', 'WINDOW_EXPIRED'].includes(i.rejection_rule));
  return (
    <section className="card" aria-labelledby="h-return">
      <div className="stack" style={{ gap: 4 }}>
        <h1 id="h-return" ref={headingRef} tabIndex={-1}>
          Return {view.return_id} {approvedAny ? (view.status === 'Completed' ? 'completed' : 'approved') : 'not approved'}
        </h1>
        <p className="muted">
          {approvedAny
            ? 'Print the label, attach it to the parcel and drop it at any courier point. Status updates when the courier scans it.'
            : 'None of the items can be returned. The reason is shown under each item.'}
        </p>
        <p className="small">Return status: <strong>{view.status}</strong> · refund to {view.refund_method === 'store_credit' ? 'store credit' : 'original payment'}</p>
      </div>

      {view.label && (
        <div className="label-box">
          <div className="qr" role="img" aria-label={`Return label QR code for ${view.label.code}`}
            dangerouslySetInnerHTML={{ __html: view.label.qr_svg }} />
          <div className="stack" style={{ gap: 6, alignItems: 'flex-start' }}>
            <strong>Mock return label · {view.label.code}</strong>
            <span className="small muted">To: {view.label.to}</span>
            <span className="small muted">Order {view.order_no}</span>
            {view.label.voided
              ? <span className="chip grey">Label voided</span>
              : <button type="button" className="btn secondary no-print" onClick={() => window.print()}>Print or save label (PDF)</button>}
          </div>
        </div>
      )}

      <div className="row between no-print">
        <h2>Status, item by item</h2>
        <button type="button" className="btn plain" onClick={refresh} disabled={busy}>{busy ? 'Refreshing…' : 'Refresh status'}</button>
      </div>
      <ErrorNote error={error} />
      {view.items.map((item) => (
        <div key={item.item_id} className="subcard">
          <div className="row between">
            <h3>{item.name}</h3>
            <StateChip state={item.state} label={item.state_label} />
          </div>
          <Timeline item={item} />
          <p className="small muted">
            {item.category_label} · paid {inr(item.price)}
            {item.amount && item.state !== 'rejected' ? ` · refund ${inr(item.amount)}${item.grade === 'C' ? ' (grade C, 50%)' : ''}` : ''}
            {item.history.length ? ` · updated ${when(item.history[item.history.length - 1].at)}` : ''}
          </p>
          {item.rejection_text && <p className="small" style={{ color: 'var(--rr-color-warning)' }}>{item.rejection_text}</p>}
          {item.may_need_finance && !['refunded', 'rejected', 'cancelled', 'expired'].includes(item.state) && (
            <p className="small" style={{ color: 'var(--rr-color-warning)' }}>
              If this item's refund is over ₹10,000 after grading, finance checks it before payment.
            </p>
          )}
          {item.can_cancel && session?.role === 'customer' && (
            <div className="no-print">
              <button type="button" className="btn danger" onClick={() => cancel(item)} disabled={busy}>
                Cancel return of this item
              </button>
            </div>
          )}
        </div>
      ))}
    </section>
  );
}

export function ReturnStatusPage() {
  const { session } = useSession();
  const { returnId } = useParams();
  const heading = useFocusHeading(returnId);
  if (!session) {
    return (
      <Layout width="narrow">
        <div className="card">
          <h1>Find your return</h1>
          <p className="muted">For your security, sign in with your order number, email and a one-time code first.</p>
          <Link className="btn primary" to="/return">Start</Link>
        </div>
      </Layout>
    );
  }
  return (
    <Layout width="narrow" menu={session.role === 'customer' && <Link to="/return">My order</Link>}>
      <ReturnStatus headingRef={heading} />
    </Layout>
  );
}
