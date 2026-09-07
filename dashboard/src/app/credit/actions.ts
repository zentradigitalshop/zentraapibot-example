"use server";

import { revalidatePath } from "next/cache";
import { requireAdmin } from "@/lib/auth";
import { sql, withRetry } from "@/lib/db";
import { creditByHand } from "@/lib/credit";

export type CreditFormState = { ok: boolean; message: string } | null;

export async function submitCredit(
  _prev: CreditFormState,
  formData: FormData,
): Promise<CreditFormState> {
  await requireAdmin();

  const telegramId = Number(formData.get("telegramId"));
  const amount = String(formData.get("amount") ?? "");
  const reason = String(formData.get("reason") ?? "");

  if (!Number.isFinite(telegramId) || telegramId <= 0) {
    return { ok: false, message: "Enter a valid Telegram id." };
  }

  const result = await withRetry(() => creditByHand(sql, { telegramId, amount, reason }));
  if (!result.ok) return { ok: false, message: result.message };

  revalidatePath("/credit");
  return { ok: true, message: `Done — new balance is $${Number(result.newBalance).toFixed(2)}.` };
}
