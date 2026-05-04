import React from "react";

export default function QueueControls({ selectedCount, onScrape, onClean, onClear }) {
  const disabled = selectedCount === 0;
  return (
    <div className="controls">
      <button onClick={onScrape} disabled={disabled}>
        Scrape selected ({selectedCount})
      </button>
      <button onClick={onClean} disabled={disabled}>
        Clean selected ({selectedCount})
      </button>
      <button onClick={onClear} disabled={disabled}>
        Clear selection
      </button>
    </div>
  );
}
