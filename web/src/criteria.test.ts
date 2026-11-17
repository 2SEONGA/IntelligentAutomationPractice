import { test } from "node:test";
import assert from "node:assert/strict";
import { formErrors, fromValues, parseList, toValues } from "./criteria.ts";

const VALUES = {
  min_amount_krw: 4_000_000_000, deadline_warn_days: 3, field_keywords: ["AI", "클라우드"],
  exclude_contract_terms: ["수의계약"], confirm_contract_terms: ["제한경쟁"], core_agencies: ["국세청"],
  s2_keywords: { "2": ["운영"], "1": ["구축"] }, decision: { go: 6, review: 4 },
};

test("parseList splits on commas and newlines, drops blanks and repeats", () => {
  assert.deepEqual(parseList("AI, 클라우드\n,AI "), ["AI", "클라우드"]);
  assert.deepEqual(parseList("  "), []);
});

test("form round-trips values, amount entered in 억", () => {
  const form = fromValues("공격적", VALUES);
  assert.equal(form.minEok, "40");
  assert.deepEqual(toValues(form), VALUES);
  assert.equal(toValues({ ...form, minEok: "20" }).min_amount_krw, 2_000_000_000);
  assert.equal(toValues({ ...form, minEok: "12.5" }).min_amount_krw, 1_250_000_000);
});

test("formErrors catches name, score order and bad numbers", () => {
  const form = fromValues("공격적", VALUES);
  assert.deepEqual(formErrors(form), []);
  assert.equal(formErrors({ ...form, name: "기본" }).length, 1);
  assert.equal(formErrors({ ...form, name: " " }).length, 1);
  assert.equal(formErrors({ ...form, go: "4", review: "4" }).length, 1);
  assert.equal(formErrors({ ...form, minEok: "-1" }).length, 1);
  assert.equal(formErrors({ ...form, minEok: "abc" }).length, 1);
  assert.equal(formErrors({ ...form, warnDays: "31" }).length, 1);
});
