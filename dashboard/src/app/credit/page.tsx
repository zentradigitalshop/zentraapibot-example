import Shell from "@/components/Shell";
import { requireAdmin } from "@/lib/auth";
import { sql, withRetry } from "@/lib/db";
import { recentAdjustments } from "@/lib/credit";
import CreditForm from "./CreditForm";

export const dynamic = "force-dynamic";
export const maxDuration = 30; // keep in sync with lib/retry.ts MAX_DURATION

export default async function CreditPage() {
  await requireAdmin();
  const adjustments = await withRetry(() => recentAdjustments(sql, 50));

  return (
    <Shell>
      <div className="page-head">
        <div>
          <h1>Credit by hand</h1>
          <p>The fallback every payment rail keeps forever — for a payment that arrived by some method the bot doesn&apos;t watch.</p>
        </div>
      </div>

      <div className="panel" style={{ maxWidth: 480, marginBottom: 20 }}>
        <div className="panel-head"><h2>New adjustment</h2></div>
        <div style={{ padding: "18px 18px 4px" }}>
          <CreditForm />
        </div>
      </div>

      <div className="panel">
        <div className="panel-head"><h2>Recent adjustments</h2></div>
        <div className="table-scroll">
          {adjustments.length === 0 ? (
            <div className="empty">No adjustments yet.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Customer</th>
                  <th>Amount</th>
                  <th>Reason</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {adjustments.map((a) => (
                  <tr key={a.id}>
                    <td>{a.username ? `@${a.username}` : <span className="mono">{a.telegram_id}</span>}</td>
                    <td className="mono">
                      {Number(a.amount_usd) >= 0 ? "+" : ""}
                      ${Number(a.amount_usd).toFixed(2)}
                    </td>
                    <td>{a.reason}</td>
                    <td className="mono">{new Date(a.created_at).toLocaleString()}</td>
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
