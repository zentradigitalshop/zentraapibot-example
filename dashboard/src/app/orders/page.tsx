import Shell from "@/components/Shell";
import { requireAdmin } from "@/lib/auth";
import { sql, withRetry } from "@/lib/db";
import { searchOrders, orderTotals } from "@/lib/orders";

export const dynamic = "force-dynamic";
export const maxDuration = 30; // keep in sync with lib/retry.ts MAX_DURATION

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  delivered: "Delivered",
  unresolved: "Unresolved",
  refunded: "Refunded",
  failed: "Failed",
};

export default async function OrdersPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  await requireAdmin();
  const { q = "" } = await searchParams;
  const rows = await withRetry(() => searchOrders(sql, q));
  const totals = orderTotals(rows);

  return (
    <Shell>
      <div className="page-head">
        <div>
          <h1>Orders</h1>
          <p>{rows.length} shown{q ? ` — matching "${q}"` : ""}.</p>
        </div>
      </div>

      <div className="cards">
        <div className="card">
          <div className="label">Delivered (shown)</div>
          <div className="value mono">{totals.orders}</div>
        </div>
        <div className="card">
          <div className="label">Revenue (shown)</div>
          <div className="value mono">${totals.revenue}</div>
        </div>
        <div className="card">
          <div className="label">Cost (shown)</div>
          <div className="value mono">${totals.cost}</div>
        </div>
        <div className="card">
          <div className="label">Profit (shown)</div>
          <div className="value mono">${totals.profit}</div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <form className="inline" method="get">
            <input
              type="text"
              name="q"
              defaultValue={q}
              placeholder="Username, Telegram id, order id, or Zentra reference"
            />
            <button type="submit">Search</button>
          </form>
        </div>
        <div className="table-scroll">
          {rows.length === 0 ? (
            <div className="empty">No orders match that search.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Order</th>
                  <th>Customer</th>
                  <th>Product</th>
                  <th>Qty</th>
                  <th>Paid</th>
                  <th>Cost</th>
                  <th>Status</th>
                  <th>Zentra ref</th>
                  <th>Placed</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td className="mono">#{row.id}</td>
                    <td>{row.username ? `@${row.username}` : <span className="mono">{row.telegram_id}</span>}</td>
                    <td>{row.product_name}</td>
                    <td className="mono">{row.quantity}</td>
                    <td className="mono">${Number(row.price_snapshot).toFixed(2)}</td>
                    <td className="mono">
                      {row.zentra_price_snapshot ? `$${Number(row.zentra_price_snapshot).toFixed(2)}` : "—"}
                    </td>
                    <td><span className={`pill pill-${row.status}`}>{STATUS_LABEL[row.status] ?? row.status}</span></td>
                    <td className="mono">{row.zentra_reference ?? "—"}</td>
                    <td className="mono">{new Date(row.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </Shell>
  );
}
