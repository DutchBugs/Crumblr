// Regression test for dashboard.html's BID/ASK odometer digit rendering
// (CONTINUOUS LIVE DASHBOARD work order §1): a stable 5-decimal display,
// digit-only roll-up/roll-down animation for changed digits, no animation
// on unchanged ticks or the first render, and correct handling of decimal
// carry (e.g. 1.09999 -> 1.10000, where every fraction digit changes at
// once).
//
// Same no-framework Node `vm` approach as the other tests in this
// directory -- runs the actual shipped <script> block, not a
// reimplementation, against a small hand-built DOM stub.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const templatePath = join(here, "..", "..", "src", "crumblr", "dashboard", "templates", "dashboard.html");
const html = readFileSync(templatePath, "utf-8");

const scriptBlocks = [...html.matchAll(/<script(?![^>]*id="state-data")[^>]*>([\s\S]*?)<\/script>/g)];
assert.ok(scriptBlocks.length >= 1, "could not find the dashboard's logic <script> block");
const scriptSource = scriptBlocks[scriptBlocks.length - 1][1];

function makeElement() {
  return { textContent: "", className: "", innerHTML: "", style: {} };
}

function makeFakeDom() {
  const elements = new Map();
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, makeElement());
      return elements.get(id);
    },
    createElementNS() {
      return { setAttribute() {}, appendChild() {} };
    },
  };
  return { document, elements };
}

const { document, elements } = makeFakeDom();
const moduleShim = { exports: {} };
const context = {
  document,
  module: moduleShim,
  fetch: () => Promise.reject(new Error("fetch must not be called by this test")),
  setInterval: () => 0,
  setTimeout: () => 0,
  console,
  Date,
  Math,
  parseFloat,
  isNaN,
  // Deliberately no `window` -- proves the reduced-motion check degrades
  // safely (REDUCED_MOTION = false) rather than throwing when `window` is
  // not defined at all, exactly like this same Node harness for every
  // other test in this directory.
};
vm.createContext(context);
vm.runInContext(scriptSource, context, { filename: "dashboard.html (inline script)" });

const { formatPrice5dp, priceCharSpecs, renderPrice } = moduleShim.exports;
assert.equal(typeof formatPrice5dp, "function", "formatPrice5dp was not exported for testing");
assert.equal(typeof priceCharSpecs, "function", "priceCharSpecs was not exported for testing");
assert.equal(typeof renderPrice, "function", "renderPrice was not exported for testing");

function digitClasses(specs) {
  return specs.filter((s) => s.ch !== ".").map((s) => s.cls);
}

// --- 1. Values arriving with more than 5 decimal places are clamped to
// exactly 5 for display -- persisted/raw precision is a separate concern
// this function never touches. ----------------------------------------
assert.equal(formatPrice5dp("1.147253217"), "1.14725");
assert.equal(formatPrice5dp("1.1472549999"), "1.14725");
assert.equal(formatPrice5dp("1.1"), "1.10000", "fewer than 5 decimals must still pad to a stable width");
assert.equal(formatPrice5dp("not-a-number"), null);

// --- 2. Upward change: every genuinely-changed digit rolls up ----------
{
  const specs = priceCharSpecs("1.14730", "1.14725", false);
  const changed = digitClasses(specs).filter((c) => c.indexOf("roll-") >= 0);
  assert.ok(changed.length > 0, "an upward change must mark at least one digit changed");
  for (const cls of changed) {
    assert.match(cls, /roll-up/, "an upward price change must roll digits up, never down");
  }
  // Unchanged leading digits ("1.147") must carry no roll class at all.
  const unchangedLeading = digitClasses(specs).slice(0, 3);
  for (const cls of unchangedLeading) {
    assert.doesNotMatch(cls, /roll-/, "unchanged leading digits must not animate");
  }
}

// --- 3. Downward change: every genuinely-changed digit rolls down ------
{
  const specs = priceCharSpecs("1.14720", "1.14725", false);
  const changed = digitClasses(specs).filter((c) => c.indexOf("roll-") >= 0);
  assert.ok(changed.length > 0, "a downward change must mark at least one digit changed");
  for (const cls of changed) {
    assert.match(cls, /roll-down/, "a downward price change must roll digits down, never up");
  }
}

// --- 4. Unchanged value: no digit animates at all -----------------------
{
  const specs = priceCharSpecs("1.14725", "1.14725", false);
  for (const cls of digitClasses(specs)) {
    assert.doesNotMatch(cls, /roll-/, "an unchanged tick must not animate any digit");
  }
}

// --- 5. Decimal carry (1.09999 -> 1.10000): every fraction digit changes,
// all in the same (upward) direction -- the classic odometer carry case. -
{
  const specs = priceCharSpecs("1.10000", "1.09999", false);
  const fractionClasses = digitClasses(specs).slice(-5); // the 5 digits after "1"
  for (const cls of fractionClasses) {
    assert.match(cls, /roll-up/, "every fraction digit must roll up on a carry from .09999 to .10000");
  }
}

// --- 6. Initial render (no previous value at all): never animates -------
{
  const specs = priceCharSpecs("1.14725", undefined, false);
  for (const cls of digitClasses(specs)) {
    assert.doesNotMatch(cls, /roll-/, "the very first render of a price must never animate");
  }
}

// --- 7. prefers-reduced-motion: even a real change never gets a roll class
{
  const specs = priceCharSpecs("1.14730", "1.14725", true);
  for (const cls of digitClasses(specs)) {
    assert.doesNotMatch(cls, /roll-/, "reduced motion must update instantly, with no animation classes");
  }
}

function spanText(htmlString) {
  return [...htmlString.matchAll(/<span[^>]*>([^<]*)<\/span>/g)].map((m) => m[1]).join("");
}

// --- 8. renderPrice(): end-to-end through the fake DOM, fixed width and no
// layout shift (element always ends up with the same digit-cell count). --
renderPrice("hero-bid", "1.147253217"); // initial: >5 decimals, first render
let html1 = elements.get("hero-bid").innerHTML;
assert.equal(spanText(html1), "1.14725", "formatted, clamped-to-5-decimal digits must be exactly right");
assert.equal((html1.match(/class="price-char digit/g) || []).length, 6, "1 integer + 5 fraction digit cells");
assert.doesNotMatch(html1, /roll-/, "initial renderPrice() call must not animate");

renderPrice("hero-bid", "1.14730"); // a real upward change
let html2 = elements.get("hero-bid").innerHTML;
assert.match(html2, /roll-up/, "a genuine change through renderPrice() must produce a roll-up class");
assert.equal((html2.match(/class="price-char digit/g) || []).length, 6, "digit-cell count is stable across updates");

const beforeUnchanged = elements.get("hero-bid").innerHTML;
renderPrice("hero-bid", "1.1473"); // same value, differently-formatted input -- still an unchanged tick
const afterUnchanged = elements.get("hero-bid").innerHTML;
assert.equal(afterUnchanged, beforeUnchanged, "an unchanged tick must not touch the DOM at all");

console.log("dashboard_price_odometer_test.mjs: all assertions passed");
