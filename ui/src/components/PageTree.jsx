import React, { useState } from "react";

export default function PageTree({ node, selected, onToggleSelect, onPick, activeId }) {
  return (
    <div className="tree">
      <Node
        node={node}
        selected={selected}
        onToggleSelect={onToggleSelect}
        onPick={onPick}
        activeId={activeId}
        depth={0}
      />
    </div>
  );
}

function Node({ node, selected, onToggleSelect, onPick, activeId, depth }) {
  const [open, setOpen] = useState(depth < 1);
  if (!node) return null;

  if (node.kind === "product") {
    return (
      <div
        className={`leaf ${activeId === node.product_id ? "selected" : ""}`}
        onClick={() => onPick(node.product_id)}
      >
        <input
          type="checkbox"
          checked={selected.has(node.product_id)}
          onChange={(e) => {
            e.stopPropagation();
            onToggleSelect(node.product_id);
          }}
          onClick={(e) => e.stopPropagation()}
        />{" "}
        {node.name}
        {node.status && <span className={`badge ${node.status}`}>{node.status}</span>}
      </div>
    );
  }

  return (
    <ul>
      <li>
        <span className="toggle" onClick={() => setOpen(!open)}>
          {open ? "▾" : "▸"}
        </span>
        {node.name}
        {open &&
          (node.children || []).map((c, i) => (
            <Node
              key={c.url || c.name || i}
              node={c}
              selected={selected}
              onToggleSelect={onToggleSelect}
              onPick={onPick}
              activeId={activeId}
              depth={depth + 1}
            />
          ))}
      </li>
    </ul>
  );
}
