import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import RetailerSidebar from "./components/RetailerSidebar.jsx";
import PageTree from "./components/PageTree.jsx";
import ProductPanel from "./components/ProductPanel.jsx";
import StatusDashboard from "./components/StatusDashboard.jsx";
import QueueControls from "./components/QueueControls.jsx";

export default function App() {
  const [retailers, setRetailers] = useState([]);
  const [retailer, setRetailer] = useState(null);
  const [tree, setTree] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [activeProduct, setActiveProduct] = useState(null);
  const [status, setStatus] = useState(null);
  const [healthy, setHealthy] = useState(false);

  useEffect(() => {
    api.retailers()
      .then((r) => {
        setRetailers(r.retailers);
        if (r.retailers.length && !retailer) setRetailer(r.retailers[0]);
      })
      .catch(() => setHealthy(false));
  }, []);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await api.status();
        if (!cancelled) {
          setStatus(s);
          setHealthy(true);
        }
      } catch {
        if (!cancelled) setHealthy(false);
      }
    };
    tick();
    const id = setInterval(tick, 3000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!retailer) return;
    api.tree(retailer).then((t) => setTree(t)).catch(() => setTree(null));
    setSelected(new Set());
  }, [retailer, status?.retailers?.[retailer]?.crawled]);

  const toggleSelect = (productId) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(productId)) next.delete(productId);
      else next.add(productId);
      return next;
    });
  };

  const onPickProduct = async (productId) => {
    try {
      const p = await api.product(productId);
      setActiveProduct(p);
    } catch {
      setActiveProduct({ product_id: productId, _missing: true });
    }
  };

  const onCrawl = () => retailer && api.crawl(retailer);
  const onScrape = async () => {
    if (!retailer || !tree) return;
    const urls = collectSelectedUrls(tree, selected);
    if (urls.length) await api.scrape(retailer, urls);
    setSelected(new Set());
  };
  const onClean = async () => {
    const ids = [...selected];
    if (ids.length) await api.clean(ids);
    setSelected(new Set());
  };

  return (
    <div className="app">
      <header>
        <h1>Food Scraper</h1>
        <span className={`health ${healthy ? "ok" : "bad"}`}>
          {healthy ? "● backend connected" : "● backend offline"}
        </span>
      </header>

      <RetailerSidebar
        retailers={retailers}
        active={retailer}
        onPick={setRetailer}
        onCrawl={onCrawl}
      />

      <main>
        <StatusDashboard status={status} retailer={retailer} />
        <QueueControls
          selectedCount={selected.size}
          onScrape={onScrape}
          onClean={onClean}
          onClear={() => setSelected(new Set())}
        />
        {tree?.tree ? (
          <PageTree
            node={tree.tree}
            selected={selected}
            onToggleSelect={toggleSelect}
            onPick={onPickProduct}
            activeId={activeProduct?.product_id}
          />
        ) : (
          <div className="empty">
            No tree yet for this retailer. Click <strong>Crawl</strong> in the sidebar to populate it.
          </div>
        )}
      </main>

      <aside className="right">
        <ProductPanel product={activeProduct} onPickProduct={onPickProduct} />
      </aside>
    </div>
  );
}

function collectSelectedUrls(node, selected) {
  const out = [];
  const walk = (n) => {
    if (!n) return;
    if (n.kind === "product" && selected.has(n.product_id)) out.push(n.url);
    (n.children || []).forEach(walk);
  };
  walk(node);
  return out;
}
