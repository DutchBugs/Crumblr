// Regression test for dashboard.html's unified Live Activity feed (work
// order §23, Slice 4): the platform-journal and PAPER_LITE activity tables
// previously only ever showed whatever the server rendered on first page
// load -- exactly the same class of bug just fixed for the broker panels --
// and there was no client-side category filter at all. This proves both:
// a poll refresh replaces the rows, and filtering shows only the rows for
// the selected category without a new network request.
//
// Same no-framework approach as dashboard_broker_refresh_test.mjs: Node's
// built-in `vm` module runs the *actual* shipped <script> block from the
// real template file against a small DOM stub.

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
};
vm.createContext(context);
vm.runInContext(scriptSource, context, { filename: "dashboard.html (inline script)" });

const { renderActivityFeed, activityCategory } = moduleShim.exports;
assert.equal(typeof renderActivityFeed, "function", "renderActivityFeed was not exported for testing");
assert.equal(typeof activityCategory, "function", "activityCategory was not exported for testing");

// --- 1. Source -> category mapping matches the actual event.source values
// this codebase writes (see state.py::_event_summary / grep "source=" ) ---
assert.equal(activityCategory("orchestration"), "MARKET");
assert.equal(activityCategory("trading_agent"), "AGENT");
assert.equal(activityCategory("agent_gateway"), "AGENT");
assert.equal(activityCategory("risk_engine"), "RISK");
assert.equal(activityCategory("supervisor"), "SUPERVISOR");
assert.equal(activityCategory("execution_orchestrator"), "EXECUTION");
assert.equal(activityCategory("paper_lite"), "PAPER");
assert.equal(activityCategory("something_unmapped"), null);

function stateWith(events, paperRows) {
  return { recent_events: events, paper_lite_activity: paperRows };
}

const riskEvent = { occurred_at_utc: "2026-09-07T12:00:00Z", component: "risk_engine", event_type: "RISK_DECISION_MADE", summary: "PASS" };
const supervisorEvent = { occurred_at_utc: "2026-09-07T12:00:01Z", component: "supervisor", event_type: "SUPERVISOR_DECISION_MADE", summary: "APPROVE" };
const paperRow = { sequence: 1, event_type: "PAPER_ORDER_ACCEPTED", detail: "filled" };

// --- 2. "ALL" shows every journal row and keeps the paper card visible ---
renderActivityFeed(stateWith([riskEvent, supervisorEvent], [paperRow]), "ALL");
let journalHtml = elements.get("activity-journal-body").innerHTML;
assert.match(journalHtml, /RISK_DECISION_MADE/);
assert.match(journalHtml, /SUPERVISOR_DECISION_MADE/);
assert.notEqual(elements.get("activity-paper-card").style.display, "none");
assert.match(elements.get("activity-paper-body").innerHTML, /PAPER_ORDER_ACCEPTED/);

// --- 3. Filtering by RISK hides the supervisor row and the paper card ----
renderActivityFeed(stateWith([riskEvent, supervisorEvent], [paperRow]), "RISK");
journalHtml = elements.get("activity-journal-body").innerHTML;
assert.match(journalHtml, /RISK_DECISION_MADE/);
assert.doesNotMatch(journalHtml, /SUPERVISOR_DECISION_MADE/, "a non-RISK row leaked through the RISK filter");
assert.equal(elements.get("activity-paper-card").style.display, "none", "paper card should be hidden under a non-PAPER filter");

// --- 4. Filtering by PAPER shows the paper card and only paper_lite rows -
const paperSourcedEvent = { occurred_at_utc: "2026-09-07T12:00:02Z", component: "paper_lite", event_type: "PAPER_LITE_SESSION_BLOCKED", summary: "blocked" };
renderActivityFeed(stateWith([riskEvent, paperSourcedEvent], [paperRow]), "PAPER");
journalHtml = elements.get("activity-journal-body").innerHTML;
assert.match(journalHtml, /PAPER_LITE_SESSION_BLOCKED/);
assert.doesNotMatch(journalHtml, /RISK_DECISION_MADE/, "a non-PAPER row leaked through the PAPER filter");
assert.notEqual(elements.get("activity-paper-card").style.display, "none");

// --- 5. A later poll's rows genuinely replace the earlier ones, not append
const laterRiskEvent = { occurred_at_utc: "2026-09-07T12:05:00Z", component: "risk_engine", event_type: "RISK_DECISION_MADE", summary: "BLOCK" };
renderActivityFeed(stateWith([laterRiskEvent], []), "ALL");
journalHtml = elements.get("activity-journal-body").innerHTML;
assert.match(journalHtml, /BLOCK/);
assert.doesNotMatch(journalHtml, /SUPERVISOR_DECISION_MADE/, "a row from an earlier poll was not cleared");
assert.doesNotMatch(journalHtml, /PAPER_LITE_SESSION_BLOCKED/, "a row from an earlier poll was not cleared");
assert.match(elements.get("activity-paper-body").innerHTML, /No PAPER_LITE audit entries yet/);

// --- 6. Empty journal under "ALL" reads as absence, not a crash ----------
renderActivityFeed(stateWith([], []), "ALL");
assert.match(elements.get("activity-journal-body").innerHTML, /No recent journal events/);

console.log("dashboard_activity_feed_test.mjs: all assertions passed");
