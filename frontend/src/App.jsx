import { useState } from 'react';
import './App.css';

const sampleOrders = [
  { id: 'AUR-1042', item: 'Hydrating Face Serum', status: 'Shipped' },
  { id: 'AUR-1087', item: 'Daily Moisturizer', status: 'Processing' },
];

function App() {
  const [isCallActive, setIsCallActive] = useState(false);

  return (
    <main className="app-shell">
      <header className="page-header">
        <p className="eyebrow">AURA SKINCARE</p>
        <h1>AI Voice Customer Support Agent</h1>
        <p className="subtitle">A simple workspace for customer conversations.</p>
      </header>

      <section className="call-card" aria-labelledby="call-heading">
        <div className="call-card-header">
          <div>
            <p className="eyebrow">VOICE SUPPORT</p>
            <h2 id="call-heading">Call controls</h2>
          </div>
          <p className={`call-status ${isCallActive ? 'active' : ''}`}>
            <span className="status-dot" aria-hidden="true" />
            {isCallActive ? 'Call in progress' : 'Call not started'}
          </p>
        </div>

        <div className="call-actions">
          <button
            className="button button-primary"
            type="button"
            onClick={() => setIsCallActive(true)}
            disabled={isCallActive}
          >
            Start Call
          </button>
          <button
            className="button button-secondary"
            type="button"
            onClick={() => setIsCallActive(false)}
            disabled={!isCallActive}
          >
            End Call
          </button>
        </div>
      </section>

      <div className="content-grid">
        <section className="panel" aria-labelledby="transcript-heading">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">LIVE NOTES</p>
              <h2 id="transcript-heading">Transcript</h2>
            </div>
            <span className="panel-label">Session</span>
          </div>
          <div className="transcript-empty">
            <span className="transcript-icon" aria-hidden="true">Aa</span>
            <p>Conversation transcript will appear here.</p>
            <span className="muted-text">No conversation yet</span>
          </div>
        </section>

        <section className="panel" aria-labelledby="orders-heading">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">CUSTOMER CARE</p>
              <h2 id="orders-heading">Sample orders</h2>
            </div>
            <span className="panel-label">{sampleOrders.length} orders</span>
          </div>
          <ul className="order-list">
            {sampleOrders.map((order) => (
              <li className="order-item" key={order.id}>
                <div>
                  <strong>{order.item}</strong>
                  <span className="order-id">{order.id}</span>
                </div>
                <span className="order-status">{order.status}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </main>
  );
}

export default App;
