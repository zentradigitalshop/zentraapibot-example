import Shell from "@/components/Shell";
import { requireAdmin } from "@/lib/auth";
import { sql, withRetry } from "@/lib/db";
import { settingsRows } from "@/lib/settings";
import SettingRowForm from "./SettingRow";

export const dynamic = "force-dynamic";
export const maxDuration = 30; // keep in sync with lib/retry.ts MAX_DURATION

export default async function SettingsPage() {
  await requireAdmin();
  const rows = await withRetry(() => settingsRows(sql));

  const byCategory = new Map<string, typeof rows>();
  for (const row of rows) {
    const list = byCategory.get(row.category) ?? [];
    list.push(row);
    byCategory.set(row.category, list);
  }

  return (
    <Shell>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>Takes effect immediately — read by the bot on its next check, no restart needed.</p>
        </div>
      </div>

      {[...byCategory.entries()].map(([category, categoryRows]) => (
        <div className="panel" key={category}>
          <div className="panel-head">
            <h2 style={{ textTransform: "capitalize" }}>{category}</h2>
          </div>
          <div>
            {categoryRows.map((row) => (
              <SettingRowForm key={row.key} row={row} />
            ))}
          </div>
        </div>
      ))}
    </Shell>
  );
}
