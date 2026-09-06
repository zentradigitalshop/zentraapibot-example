"use server";

import { revalidatePath } from "next/cache";
import { requireAdmin } from "@/lib/auth";
import { sql, withRetry } from "@/lib/db";
import { writeSetting } from "@/lib/settings";

export type SettingFormState = { ok: boolean; message: string; key: string } | null;

export async function saveSetting(
  _prev: SettingFormState,
  formData: FormData,
): Promise<SettingFormState> {
  await requireAdmin();

  const key = String(formData.get("key") ?? "");
  const value = String(formData.get("value") ?? "");
  if (!key) return { ok: false, key, message: "Missing setting key." };

  const result = await withRetry(() => writeSetting(sql, key, value));
  revalidatePath("/settings");
  return result;
}
