import type { Sql } from "postgres";

/**
 * Everything about changing a setting: the rules, and the write.
 *
 * Kept apart from the server action so it can be tested directly — against
 * a real database for the write, and with no database at all for the rules.
 *
 * THE RULES MUST AGREE WITH bot/settings.py's OWN PARSING. Not because the
 * two are generated from one source — they are not — but because both read
 * their bounds and type from the same database row, so the only thing that
 * can drift is this small amount of interpretation.
 */

export type SettingRow = {
  key: string;
  value: string | null;
  default_value: string;
  value_type: string;
  min_value: string | null;
  max_value: string | null;
  category: string;
  label: string;
  help: string;
  sort_order: number;
  updated_at: string | null;
};

const TRUE_WORDS = ["yes", "true", "on", "1"];
const FALSE_WORDS = ["no", "false", "off", "0"];

/** Trailing zeros off a decimal bound, for a clean error message. */
export function tidy(value: string | null): string {
  if (value === null) return "";
  if (!value.includes(".")) return value;
  return value.replace(/0+$/, "").replace(/\.$/, "");
}

export function parse(
  row: Pick<SettingRow, "value_type" | "min_value" | "max_value">,
  raw: string,
): { value?: string; error?: string } {
  const text = (raw ?? "").trim();

  if (row.value_type === "bool") {
    if (TRUE_WORDS.includes(text.toLowerCase())) return { value: "yes" };
    if (FALSE_WORDS.includes(text.toLowerCase())) return { value: "no" };
    return { error: "Must be yes or no." };
  }

  if (row.value_type === "text") {
    if (!text) return { error: "This cannot be empty." };
    return { value: text };
  }

  if (!/^-?\d+(\.\d+)?$/.test(text)) return { error: "Must be a number." };
  const number = Number(text);

  if (row.value_type === "int" && !Number.isInteger(number)) {
    return { error: "Must be a whole number." };
  }
  if (row.min_value !== null && number < Number(row.min_value)) {
    return { error: `Must be at least ${tidy(row.min_value)}.` };
  }
  if (row.max_value !== null && number > Number(row.max_value)) {
    return { error: `Must be at most ${tidy(row.max_value)}.` };
  }

  return { value: row.value_type === "int" ? String(Math.trunc(number)) : text };
}

type Client = Sql<Record<string, never>>;

export type Result = { ok: boolean; message: string; key: string };

export async function writeSetting(sql: Client, key: string, raw: string): Promise<Result> {
  const [row] = await sql<SettingRow[]>`SELECT * FROM settings WHERE key = ${key}`;
  if (!row) return { ok: false, key, message: "No such setting." };

  const parsed = parse(row, raw);
  if (parsed.error || parsed.value === undefined) {
    return { ok: false, key, message: parsed.error ?? "Invalid value." };
  }

  const previous = row.value;
  if (previous === parsed.value) return { ok: true, key, message: "Unchanged." };

  // changed_by references users(id), a Telegram customer — and this
  // dashboard signs in with a password, not a Telegram account, so there is
  // no id to attach. The history row still records WHAT changed and WHEN,
  // in the same transaction as the value itself, so the two can never
  // disagree about whether a change happened.
  await sql.begin(async (tx) => {
    await tx`UPDATE settings SET value = ${parsed.value!}, updated_at = now() WHERE key = ${key}`;
    await tx`
      INSERT INTO settings_history (key, old_value, new_value, changed_by)
      VALUES (${key}, ${previous}, ${parsed.value!}, NULL)
    `;
  });

  return { ok: true, key, message: "Saved." };
}

export async function settingsRows(sql: Client): Promise<SettingRow[]> {
  return sql<SettingRow[]>`SELECT * FROM settings ORDER BY category, sort_order`;
}
