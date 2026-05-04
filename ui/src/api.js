const BASE = "/api";

async function req(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${path}: ${text}`);
  }
  return res.json();
}

export const api = {
  health: () => req("/health"),
  status: () => req("/status"),
  retailers: () => req("/retailers"),
  tree: (retailer) => req(`/tree/${retailer}`),
  crawl: (retailer) => req(`/crawl/${retailer}`, { method: "POST" }),
  scrape: (retailer, urls, force = false) =>
    req(`/scrape`, {
      method: "POST",
      body: JSON.stringify({ retailer, urls, force }),
    }),
  clean: (productIds) =>
    req(`/clean`, {
      method: "POST",
      body: JSON.stringify({ product_ids: productIds }),
    }),
  product: (id) => req(`/product/${id}`),
  flag: (id, flagged) =>
    req(`/product/${id}/flag`, {
      method: "POST",
      body: JSON.stringify({ flagged }),
    }),
};
