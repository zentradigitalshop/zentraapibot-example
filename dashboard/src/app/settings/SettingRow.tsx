"use client";

import { useActionState } from "react";
import { saveSetting, type SettingFormState } from "./actions";
import type { SettingRow as Row } from "@/lib/settings";

const initialState: SettingFormState = null;

export default function SettingRowForm({ row }: { row: Row }) {
  const [state, formAction, pending] = useActionState(saveSetting, initialState);
  const current = row.value ?? row.default_value;

  return (
    <form action={formAction} className="inline" style={{ padding: "12px 18px", borderTop: "1px solid var(--border)" }}>
      <input type="hidden" name="key" value={row.key} />
      <div style={{ flex: "1 1 260px" }}>
        <div style={{ fontWeight: 600, fontSize: 13 }}>{row.label}</div>
        <div className="help">{row.help}</div>
        {state && state.key === row.key && (
          <div className={state.ok ? "mono" : "mono"} style={{ color: state.ok ? "var(--accent)" : "var(--danger)", fontSize: 12, marginTop: 4 }}>
            {state.message}
          </div>
        )}
      </div>

      {row.value_type === "bool" ? (
        <select name="value" defaultValue={current} style={{ minWidth: 90 }}>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      ) : (
        <input
          type="text"
          name="value"
          defaultValue={current}
          inputMode={row.value_type === "text" ? "text" : "decimal"}
          style={{ width: 120 }}
        />
      )}

      <button type="submit" disabled={pending}>{pending ? "Saving…" : "Save"}</button>
    </form>
  );
}
