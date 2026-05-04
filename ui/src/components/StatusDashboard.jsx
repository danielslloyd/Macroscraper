import React from "react";

export default function StatusDashboard({ status, retailer }) {
  if (!status) return <div className="empty">Loading status…</div>;
  const q = status.queue || {};
  const r = (retailer && status.retailers?.[retailer]) || {
    crawled: 0,
    scraped: 0,
    cleaned: 0,
    flagged: 0,
  };
  return (
    <>
      <div className="dashboard">
        <Card label="Pending scrape" value={q.pending_scrape ?? 0} />
        <Card label="Pending clean" value={q.pending_clean ?? 0} />
        <Card label="Processing" value={q.processing ?? 0} />
        <Card label={`${retailer || "—"} crawled`} value={r.crawled} />
        <Card label={`${retailer || "—"} scraped`} value={r.scraped} />
        <Card label={`${retailer || "—"} cleaned`} value={r.cleaned} />
      </div>
      <div className="card activity">
        <div className="label" style={{ marginBottom: 6 }}>Recent activity</div>
        {(status.activity || []).length === 0 && (
          <div style={{ color: "var(--muted)", fontSize: 12 }}>No jobs yet.</div>
        )}
        {(status.activity || []).map((a) => (
          <div key={a.id} className={`row ${a.status}`}>
            <span>{a.type}</span>
            <span>{a.activity_label || a.product_id || a.retailer || "—"}</span>
            <span>{a.status}</span>
          </div>
        ))}
      </div>
    </>
  );
}

function Card({ label, value }) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}
