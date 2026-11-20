import { useEffect, useRef } from 'react';
import { Link, Navigate } from 'react-router';
import { HOME_FOR_ROLE, ROLE_LABEL, useSession } from './session.jsx';

export function Layout({ width = 'medium', menu, children }) {
  const { session, signOut } = useSession();
  return (
    <>
      <a href="#main" className="skip">Skip to main content</a>
      <header className="header">
        <Link to="/" className="brand">ReturnRight</Link>
        <nav aria-label="Main menu" className="nav">
          {menu}
          {session && session.role !== 'customer' && (
            <span className="muted">{session.name} · {ROLE_LABEL[session.role]}</span>
          )}
          {session && <Link to="/" onClick={signOut} style={{ fontWeight: 600, padding: '10px 0' }}>Sign out</Link>}
        </nav>
      </header>
      <main id="main" tabIndex={-1} className={`main ${width}`}>{children}</main>
    </>
  );
}

/** Staff route guard: only the matching role sees the screen (the API enforces it too). */
export function RequireRole({ role, children }) {
  const { session } = useSession();
  if (!session) return <Navigate to="/staff" replace state={{ next: HOME_FOR_ROLE[role] }} />;
  if (session.role !== role) {
    return (
      <Layout width="narrow">
        <div className="card" role="alert">
          <h1>This screen isn't for your role</h1>
          <p className="muted">You're signed in as {ROLE_LABEL[session.role]}. Each role sees only its own screens.</p>
          <Link className="btn primary" to={HOME_FOR_ROLE[session.role]}>Go to my screen</Link>
        </div>
      </Layout>
    );
  }
  return children;
}

export function Loading({ label = 'Loading…' }) {
  return (
    <div className="row" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" /> <span className="muted">{label}</span>
    </div>
  );
}

export function ErrorNote({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="alert warn row between" role="alert">
      <span>{error.message || String(error)}</span>
      {onRetry && <button type="button" className="btn plain" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Empty({ title, children }) {
  return (
    <div className="placeholder" role="status">
      <div className="stack" style={{ alignItems: 'center' }}>
        <strong>{title}</strong>
        {children && <span>{children}</span>}
      </div>
    </div>
  );
}

const STATE_TONE = {
  refunded: 'green', rejected: 'orange', cancelled: 'grey', expired: 'grey', refund_failed: 'orange',
  pending_finance: 'orange', refund_pending: 'blue',
};

export function StateChip({ state, label }) {
  return <span className={`chip ${STATE_TONE[state] || 'blue'}`}>{label}</span>;
}

const LABELS = {
  requested: 'Requested', approved: 'Approved', in_transit: 'In transit', received: 'Received', inspected: 'Inspected',
  pending_finance: 'Pending finance', refund_pending: 'Refund pending', refund_failed: 'Refund failed',
  refunded: 'Refunded', rejected: 'Rejected', cancelled: 'Cancelled', expired: 'Expired',
};
const BAD = new Set(['rejected', 'cancelled', 'expired', 'refund_failed']);

/** Status timeline for one item: done, current, skipped (scan without carrier event) and still to come. */
export function Timeline({ item }) {
  const reached = new Set(item.history.map((h) => h.state));
  const steps = [...item.path];
  const current = item.state;
  if (current === 'refund_pending' && !steps.includes(current)) steps.splice(steps.indexOf('refunded'), 0, current);
  else if (!steps.includes(current)) {
    const last = Math.max(...steps.map((s, i) => (reached.has(s) ? i : -1)));
    steps.splice(last + 1, steps.length, current);
  }
  const currentIdx = steps.indexOf(current);
  return (
    <ol className="timeline" aria-label={`Status of ${item.name}`}>
      {steps.map((s, i) => {
        let cls = 'todo';
        let sr = ' (not yet)';
        if (s === current) {
          cls = `current${BAD.has(s) ? ' bad' : s === 'refunded' ? ' good' : ''}`;
          sr = ' (current status)';
        } else if (reached.has(s)) { cls = 'done'; sr = ' (done)'; }
        else if (i < currentIdx) { cls = 'skipped'; sr = ' (skipped)'; }
        return (
          <li key={s} className={cls} aria-current={s === current ? 'step' : undefined}>
            {cls === 'done' ? '✓ ' : ''}{LABELS[s]}<span className="sr-only">{sr}</span>
          </li>
        );
      })}
    </ol>
  );
}

/** Moves keyboard focus to the heading when the step/view changes (screen-reader friendly). */
export function useFocusHeading(dep) {
  const ref = useRef(null);
  useEffect(() => {
    ref.current?.focus();
  }, [dep]);
  return ref;
}

export function Bars({ rows, max, label }) {
  const top = max || Math.max(1, ...rows.map((r) => r.value || 0));
  return (
    <ul className="bars" aria-label={label}>
      {rows.map((r) => (
        <li key={r.name}>
          <span>{r.name}</span>
          <div className="track" aria-hidden="true">
            <div className="fill" style={{ width: `${Math.min(100, ((r.value || 0) / top) * 100)}%` }} />
          </div>
          <span className="val">{r.text}</span>
        </li>
      ))}
    </ul>
  );
}
