import { Link } from 'react-router';
import { Layout } from '../ui.jsx';

export default function Home() {
  return (
    <Layout width="medium">
      <div className="stack">
        <h1 style={{ fontSize: 40 }}>Start a return or sign in to work the queue</h1>
        <p className="muted" style={{ fontSize: 17, maxWidth: 640 }}>
          Customers need only their order number and email. Staff sign in and see the screens for their role.
        </p>
      </div>
      <nav aria-label="Choose how to continue">
        <ul className="home-links">
          <li>
            <Link to="/return" className="primary">
              <span className="kicker">Customer</span>
              <span className="title">Return an item</span>
              <span>Find your order, check what can be returned, get a label.</span>
            </Link>
          </li>
          <li>
            <Link to="/staff">
              <span className="kicker muted">Staff</span>
              <span className="title">Sign in</span>
              <span className="muted">Warehouse, finance and operations staff. You'll see only your role's screens.</span>
            </Link>
          </li>
        </ul>
      </nav>
    </Layout>
  );
}
