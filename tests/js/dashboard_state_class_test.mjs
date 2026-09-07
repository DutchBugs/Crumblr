// Regression test: the browser's stateClass() must classify every value
// exactly the way app.py::state_class() does. Owner review of commit
// 1d0687e found the JS version had silently drifted into "good, warn, or
// else bad" -- so any value not explicitly listed (SnapshotCompleteness
// .COMPLETE, NO_TRADE, ...) rendered as an alarming "bad" purely because
// nothing else claimed it, not because it was ever meant to read as unsafe.
//
// Same no-framework Node `vm` approach as the other tests in this
// directory -- runs the actual shipped <script> block, not a
// reimplementation.

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

const moduleShim = { exports: {} };
const context = {
  document: { getElementById() { return null; }, createElementNS() { return { setAttribute() {}, appendChild() {} }; } },
  module: moduleShim,
  fetch: () => Promise.reject(new Error("fetch must not be called by this test")),
  setInterval: () => 0,
  setTimeout: () => 0,
  console,
  Date,
  Math,
  parseFloat,
};
vm.createContext(context);
vm.runInContext(scriptSource, context, { filename: "dashboard.html (inline script)" });

const { stateClass } = moduleShim.exports;
assert.equal(typeof stateClass, "function", "stateClass was not exported for testing");

// --- The four cases owner review named explicitly -------------------------
assert.equal(stateClass("COMPLETE"), "neutral", "SnapshotCompleteness.COMPLETE must read as neutral, not bad");
assert.equal(stateClass("NO_TRADE"), "neutral", "NO_TRADE is a normal strategy result, not a warning or an error");
assert.equal(stateClass("CONFIG GATES OPEN"), "warn", "CONFIG GATES OPEN must be explicitly warn, not fall through to bad");
assert.equal(stateClass("UNKNOWN"), "bad", "UNKNOWN must be explicitly bad, not merely the fallback");

// --- Full parity with app.py::state_class()'s three explicit sets --------
const GOOD = ["CONNECTED", "HEALTHY", "RUNNING", "GOOD", "MATCHED", "ACTIVE", "PAPER_FILLED"];
const WARN = [
  "STALE", "UNCALIBRATED", "WAITING", "NOT_YET_VALID", "AWAITING_OUTCOME", "AWAITING_EVIDENCE",
  "CONFIG GATES OPEN",
];
const BAD = [
  "DISCONNECTED", "HALTED", "UNKNOWN", "MISMATCHED", "DOWN", "UNHEALTHY", "NOT PROVISIONED",
  "EXPIRED", "DISABLED", "DEGRADED", "GATEWAY_REJECTED", "RISK_BLOCKED", "SESSION_BLOCKED",
  "POLICY_BLOCKED", "PAPER_ORDER_CHECK_BLOCKED",
];
for (const value of GOOD) {
  assert.equal(stateClass(value), "good", value + " should be good");
}
for (const value of WARN) {
  assert.equal(stateClass(value), "warn", value + " should be warn");
}
for (const value of BAD) {
  assert.equal(stateClass(value), "bad", value + " should be bad");
}

// --- Anything unlisted falls to neutral, the same default state_class()
// itself uses -- never an implicit "bad" ----------------------------------
for (const value of ["COMPLETE", "NO_TRADE", "SOME_FUTURE_VALUE_NOBODY_HAS_CLASSIFIED_YET", ""]) {
  assert.equal(stateClass(value), "neutral", value + " should be the neutral fallback");
}

// --- Case-insensitivity, matching app.py's (value or "").upper() ---------
assert.equal(stateClass("healthy"), "good");
assert.equal(stateClass("unknown"), "bad");

console.log("dashboard_state_class_test.mjs: all assertions passed");
