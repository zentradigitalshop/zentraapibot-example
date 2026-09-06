import Shell from "@/components/Shell";
import { requireAdmin } from "@/lib/auth";
import { sql, withRetry } from "@/lib/db";
import { searchCustomers } from "@/lib/customers";

export const dynamic = "force-dynamic";
export const maxDuration = 30; // keep in sync with lib/retry.ts MAX_DURATION

export default async function CustomersPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  await requireAdmin();
  const { q = "" } = await searchParams;
  const rows = await withRetry(() => searchCustomers(sql, q));

  return (
    <Shell>
      <div className="page-head">
        <div>
          <h1>Customers</h1>
          <p>{rows.length} shown, most recent first{q ? ` — matching "${q}"` : ""}.</p>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <form className="inline" method="get">
            <input type="text" name="q" defaultValue={q} placeholder="Username or Telegram id" />
            <button type="submit">Search</button>
          </form>
        </div>
        <div className="table-scroll">
          {rows.length === 0 ? (
            <div className="empty">No customers match that search.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Customer</th>
                  <th>Telegram id</th>
                  <th>Balance</th>
                  <th>Orders delivered</th>
                  <th>Total spent</th>
                  <th>Status</th>
                  <th>Since</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td>{row.username ? `@${row.username}` : <span className="mono">{row.telegram_id}</span>}</td>
                    <td className="mono">{row.telegram_id}</td>
                    <td className="mono">${Number(row.balance_usd).toFixed(2)}</td>
                    <td className="mono">{row.orders}</td>
                    <td className="mono">${Number(row.spent).toFixed(2)}</td>
                    <td>
                      {row.banned
                        ? <span className="pill pill-failed">Banned</span>
                        : <span className="pill pill-delivered">Active</span>}
                    </td>
                    <td className="mono">{new Date(row.created_at).toLocaleDateString()}</td>
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
