"use client";

import { useActionState } from "react";
import { submitCredit, type CreditFormState } from "./actions";

const initialState: CreditFormState = null;

export default function CreditForm() {
  const [state, formAction, pending] = useActionState(submitCredit, initialState);

  return (
    <form action={formAction}>
      {state && (
        <div className={`notice ${state.ok ? "notice-ok" : "notice-error"}`}>{state.message}</div>
      )}

      <div className="field">
        <label htmlFor="telegramId">Telegram id</label>
        <input id="telegramId" name="telegramId" type="number" required />
        <span className="help">Ask the customer to send /start to the bot and read it from there, or find them on the Customers page.</span>
      </div>

      <div className="field">
        <label htmlFor="amount">Amount (USDT)</label>
        <input id="amount" name="amount" type="text" inputMode="decimal" placeholder="10.00 or -5.00" required />
        <span className="help">Positive credits the customer; negative debits them. A debit that would take the balance below zero is refused.</span>
      </div>

      <div className="field">
        <label htmlFor="reason">Reason</label>
        <input id="reason" name="reason" type="text" placeholder="Manual bank transfer, receipt #4471" required />
        <span className="help">Required — this is what you, or a future admin, will read back when asking why a balance changed.</span>
      </div>

      <button type="submit" disabled={pending}>{pending ? "Applying…" : "Apply"}</button>
    </form>
  );
}
