import React from "react";
import { api } from "../api.js";

export default function ProductPanel({ product, onPickProduct }) {
  if (!product) {
    return <div className="empty">Select a product to view details.</div>;
  }
  if (product._missing) {
    return (
      <div className="empty">
        Product <code>{product.product_id}</code> has not been scraped yet.
      </div>
    );
  }
  const flag = async () => {
    await api.flag(product.product_id, product.status !== "flagged");
  };
  const queueScrape = (url) => api.scrape(product.retailer, [url]);
  return (
    <div className="product-panel">
      <h2>{product.name || "(unnamed)"}</h2>
      <div className="brand">{product.brand}</div>
      <div className="field"><span className="k">Status</span><span className={`badge ${product.status}`}>{product.status}</span></div>
      <div className="field"><span className="k">Retailer</span><span>{product.retailer}</span></div>
      <div className="field"><span className="k">URL</span><a href={product.url} target="_blank" rel="noreferrer">link</a></div>
      {product.price?.package_price != null && (
        <div className="field"><span className="k">Price</span><span>${product.price.package_price} {product.price.package_size}</span></div>
      )}

      {product.nutrition && Object.keys(product.nutrition).length > 0 && (
        <section>
          <h3>Nutrition (raw scrape)</h3>
          <div className="nutrition">
            {Object.entries(product.nutrition).map(([k, v]) => (
              <div key={k} className="field"><span className="k">{k}</span><span>{String(v)}</span></div>
            ))}
          </div>
        </section>
      )}

      {product.images?.length > 0 && (
        <section>
          <h3>Images</h3>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {product.images.map((img, i) => (
              <a key={i} href={img.public_url} target="_blank" rel="noreferrer">
                <img src={img.public_url} alt="" style={{ width: 56, height: 56, objectFit: "cover", borderRadius: 4 }} />
              </a>
            ))}
          </div>
        </section>
      )}

      {product.similar_items?.length > 0 && (
        <section>
          <h3>Similar items</h3>
          <ul className="similar">
            {product.similar_items.map((s, i) => (
              <li key={i}>
                {s.product_id ? (
                  <a onClick={() => onPickProduct(s.product_id)} style={{ cursor: "pointer" }}>{s.name}</a>
                ) : (
                  <>
                    <a href={s.public_url} target="_blank" rel="noreferrer">{s.name}</a>{" "}
                    <button
                      style={{ fontSize: 10, marginLeft: 4 }}
                      onClick={() => queueScrape(s.public_url)}
                    >
                      Queue scrape
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {product.related_products?.length > 0 && (
        <section>
          <h3>Related products</h3>
          <ul className="related">
            {product.related_products.map((id) => (
              <li key={id}>
                <a onClick={() => onPickProduct(id)} style={{ cursor: "pointer" }}>{id}</a>
              </li>
            ))}
          </ul>
        </section>
      )}

      {product.cleaning_runs?.length > 0 && (
        <section className="cleaning-runs">
          <h3>Cleaning runs</h3>
          {product.cleaning_runs.map((run, i) => (
            <div key={i} style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, color: "var(--muted)" }}>
                {run.model} · {run.run_at} {run.vision ? "· vision" : ""}
                {run.error && <span style={{ color: "var(--bad)" }}> · error</span>}
              </div>
              <pre>{JSON.stringify(run.output?.parsed ?? run.output?.raw ?? run.output, null, 2)}</pre>
            </div>
          ))}
        </section>
      )}

      <section>
        <button onClick={flag} style={{ background: "var(--panel-2)", color: "var(--text)", border: "1px solid var(--border)", padding: "6px 12px", borderRadius: 6, cursor: "pointer" }}>
          {product.status === "flagged" ? "Unflag" : "Flag for review"}
        </button>
      </section>
    </div>
  );
}
