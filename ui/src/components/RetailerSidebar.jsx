import React from "react";

const PRETTY = {
  whole_foods: "Whole Foods",
  trader_joes: "Trader Joe's",
  walmart: "Walmart",
  costco: "Costco",
  amazon: "Amazon",
  kroger: "Kroger",
  publix: "Publix",
};

export default function RetailerSidebar({ retailers, active, onPick, onCrawl }) {
  return (
    <nav className="sidebar">
      <h2>Retailers</h2>
      {retailers.map((r) => (
        <div key={r}>
          <button
            className={r === active ? "active" : ""}
            onClick={() => onPick(r)}
          >
            {PRETTY[r] || r}
          </button>
          {r === active && (
            <button className="crawl-btn" onClick={onCrawl}>
              Crawl now
            </button>
          )}
        </div>
      ))}
    </nav>
  );
}
