/**
 * The dashboard's setting validation — checked with no database at all —
 * and the write path, checked against a real one.
 *
 * THE RULES MUST AGREE WITH bot/settings.py's OWN PARSING. This does not
 * guarantee that on its own; it is what keeps a human honest about it.
 *
 * Run:  node --test tests/settings.test.mjs
 *       TEST_DATABASE_URL=postgresql://… node --test tests/settings.test.mjs
 */

import assert from "node:assert/strict";
import test from "node:test";
import postgres from "postgres";
import { parse, writeSetting } from "../src/lib/settings.ts";

const bool = { value_type: "bool", min_value: null, max_value: null };
const int = (min, max) => ({ value_type: "int", min_value: min, max_value: max });
const decimal = (min, max) => ({ value_type: "decimal", min_value: min, max_value: max });

// ---- parse(): no database -----------------------------------------------

test("yes/no in every spelling the bot itself accepts", () => {
  for (const word of ["yes", "YES", "true", "on", "1"]) {
    assert.deepEqual(parse(bool, word), { value: "yes" }, word);
  }
  for (const word of ["no", "false", "off", "0"]) {
    assert.deepEqual(parse(bool, word), { value: "no" }, word);
  }
  assert.equal(parse(bool, "maybe").error, "Must be yes or no.");
});

test("a decimal respects its own bounds", () => {
  assert.deepEqual(parse(decimal("0", "500"), "20"), { value: "20" });
  assert.equal(parse(decimal("0", "500"), "-1").error, "Must be at least 0.");
  assert.equal(parse(decimal("0", "500"), "501").error, "Must be at most 500.");
});

test("an int refuses a fraction", () => {
  assert.equal(parse(int(1, 99), "5.5").error, "Must be a whole number.");
  assert.deepEqual(parse(int(1, 99), "5"), { value: "5" });
});

test("garbage is never silently accepted as zero", () => {
  assert.equal(parse(decimal(null, null), "not a number").error, "Must be a number.");
  assert.equal(parse(decimal(null, null), "").error, "Must be a number.");
});

test("whitespace around a value does not change the answer", () => {
  assert.deepEqual(parse(int(1, 99), "  20  "), { value: "20" });
});

// ---- writeSetting(): a real database -----------------------------------

const url = process.env.TEST_DATABASE_URL;

if (!url) {
  test("writeSetting", { skip: "TEST_DATABASE_URL is not set" }, () => {});
} else {
  if (url.includes("supabase.co") || url.includes("supabase.com")) {
    throw new Error("TEST_DATABASE_URL points at a hosted Supabase project.");
  }
  const sql = postgres(url, {
    max: 2, prepare: false,
    types: { numeric: { to: 1700, from: [1700], serialize: (x) => x, parse: (x) => x } },
  });

  const read = async (key) => {
    const [row] = await sql`SELECT value FROM settings WHERE key = ${key}`;
    return row?.value ?? null;
  };

  test("a valid write changes the value and is journaled", async () => {
    await sql`UPDATE settings SET value = '20' WHERE key = 'markup_pct'`;
    const result = await writeSetting(sql, "markup_pct", "35");
    assert.equal(result.ok, true, result.message);
    assert.equal(await read("markup_pct"), "35");

    const [history] = await sql`
      SELECT * FROM settings_history WHERE key = 'markup_pct'
       ORDER BY changed_at DESC LIMIT 1`;
    assert.equal(history.old_value, "20");
    assert.equal(history.new_value, "35");
  });

  test("an out-of-bounds write is refused and changes nothing", async () => {
    await sql`UPDATE settings SET value = '20' WHERE key = 'markup_pct'`;
    const result = await writeSetting(sql, "markup_pct", "9999");
    assert.equal(result.ok, false);
    assert.match(result.message, /at most 500/);
    assert.equal(await read("markup_pct"), "20");
  });

  test("writing the current value is a no-op that still reports success", async () => {
    await sql`UPDATE settings SET value = '20' WHERE key = 'markup_pct'`;
    const before = await sql`SELECT count(*)::int AS n FROM settings_history`;
    const result = await writeSetting(sql, "markup_pct", "20");
    assert.equal(result.ok, true);
    assert.equal(result.message, "Unchanged.");
    const after = await sql`SELECT count(*)::int AS n FROM settings_history`;
    assert.equal(after[0].n, before[0].n, "an unchanged write still wrote history");
  });

  test("a setting nobody defined is refused, not silently created", async () => {
    const result = await writeSetting(sql, "not_a_real_setting", "1");
    assert.equal(result.ok, false);
    assert.equal(result.message, "No such setting.");
  });

  test.after(async () => {
    await sql`UPDATE settings SET value = NULL WHERE key = 'markup_pct'`;
    await sql`DELETE FROM settings_history WHERE key = 'markup_pct'`;
    await sql.end();
  });
}
