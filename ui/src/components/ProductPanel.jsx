import React from "react";
import { api } from "../api.js";

// Labels for the extraction tiers recorded in `nutrition_sources[]`.
const SOURCE_LABELS = {
  page_html: "retailer's own page data",
  barcode_db: "barcode lookup",
  name_db: "name match in Open Food Facts",
  ocr: "OCR of the panel photo",
  local_vision: "local vision model",
  claude: "Claude",
};

function confidenceClass(value) {
  if (value >= 0.75) return "cleaned";
  if (value > 0) return "scraped";
  return "flagged";
}

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

      {(product.sold_by || product.gtin || product.upc) && (
        <>
          {product.sold_by && (
            <div className="field">
              <span className="k">Sold by</span>
              <span>
                {product.sold_by}
                {product.first_party === false && " (third party)"}
              </span>
            </div>
          )}
          {(product.gtin || product.upc) && (
            <div className="field"><span className="k">Barcode</span><span>{product.gtin || product.upc}</span></div>
          )}
        </>
      )}

      {product.flag_reason && (
        <div className="field">
          <span className="k">Flagged</span>
          <span>{product.flag_reason}{product.flag_detail ? ` — ${product.flag_detail}` : ""}</span>
        </div>
      )}

      {product.nutrition && Object.keys(product.nutrition).length > 0 && (
        <section>
          <h3>
            Nutrition{" "}
            {product.nutrition_confidence != null && (
              <span className={`badge ${confidenceClass(product.nutrition_confidence)}`}>
                {Math.round(product.nutrition_confidence * 100)}% confidence
              </span>
            )}
          </h3>
          <div className="nutrition">
            {Object.entries(product.nutrition).map(([k, v]) => (
              <div key={k} className="field"><span className="k">{k}</span><span>{String(v)}</span></div>
            ))}
          </div>
        </section>
      )}

      {product.nutrition_sources?.length > 0 && (
        <section>
          <h3>Where the numbers came from</h3>
          <ul className="similar">
            {product.nutrition_sources.map((src, i) => (
              <li key={i} style={{ color: src.accepted ? "var(--text)" : "var(--muted)" }}>
                <span className="k">tier {src.tier}</span> {SOURCE_LABELS[src.source] || src.source}
                {src.accepted
                  ? ` — ${Object.keys(src.fields || {}).length} fields used`
                  : ` — ${src.skipped || src.rejected_reason || src.error || "nothing found"}`}
                {src.database && src.matched_name && (
                  <div style={{ fontSize: 10 }}>
                    matched “{src.matched_name}”
                    {src.match_score != null && ` (score ${src.match_score})`}
                  </div>
                )}
                {src.image && <div style={{ fontSize: 10 }}>read from {src.image}</div>}
              </li>
            ))}
          </ul>
          {product.nutrition_conflicts?.length > 0 && (
            <div style={{ fontSize: 11, color: "var(--muted)" }}>
              Disagreements kept out of the record:{" "}
              {product.nutrition_conflicts
                .map((c) => `${c.field} ${c.kept} (kept) vs ${c.rejected} from ${c.from}`)
                .join("; ")}
            </div>
          )}
        </section>
      )}

      {product.images?.length > 0 && (
        <section>
          <h3>Images</h3>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {product.images.map((img, i) => (
              <a
                key={i}
                href={img.public_url}
                target="_blank"
                rel="noreferrer"
                title={`${i}: ${img.role || "other"}`}
              >
                <img src={img.public_url} alt={img.role || ""} style={{ width: 56, height: 56, objectFit: "cover", borderRadius: 4 }} />
              </a>
            ))}
          </div>
          {product.nutrition_label_image?.local_path && (
            <div style={{ fontSize: 11, color: "var(--muted)" }}>
              Nutrition panel: image {product.nutrition_label_image.image_index ?? "—"}
              {product.nutrition_label_image.panel_likeness != null &&
                ` (panel match ${product.nutrition_label_image.panel_likeness})`}
            </div>
          )}
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
