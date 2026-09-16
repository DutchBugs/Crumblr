// FULL RUN 1: renderTrainerPanel()/renderExecutionActivity() must actually
// rebuild their containers from state.trainer_panel/state.execution_activity/
// state.decision_outcome_counts on every poll -- the same "server-rendered
// once, then never touched by applyState()" gap already fixed for the
// broker/pipeline/activity panels. Same no-framework Node `vm` approach as
// the other tests in this directory -- runs the actual shipped <script>
// block, not a reimplementation.

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
  return { innerHTML: "" };
}

function makeElementWithStyle() {
  return { innerHTML: "", textContent: "", style: { display: "" } };
}

function makeDom() {
  const elements = {
    "decision-outcome-counts": makeElement(),
    "agent-active-badge": makeElement(),
    "trainer-campaign-body": makeElement(),
    "trainer-dataset-body": makeElement(),
    "trainer-candidate-body": makeElement(),
    "execution-activity-body": makeElement(),
    "run-coherence-card": makeElementWithStyle(),
    "run-coherence-detail": makeElement(),
  };
  return {
    getElementById(id) {
      return elements[id] || null;
    },
    createElementNS() {
      return { setAttribute() {}, appendChild() {} };
    },
    elements,
  };
}

const dom = makeDom();
const moduleShim = { exports: {} };
const context = {
  document: dom,
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

const { renderTrainerPanel, renderExecutionActivity } = moduleShim.exports;
assert.equal(typeof renderTrainerPanel, "function", "renderTrainerPanel was not exported for testing");
assert.equal(
  typeof renderExecutionActivity,
  "function",
  "renderExecutionActivity was not exported for testing"
);

// --- decision outcome counts + ACTIVE STATIC TRADER badge -----------------
renderTrainerPanel({
  decision_outcome_counts: { NO_TRADE: 4892, TRADE_PROPOSAL: 1 },
  agent_panel: { assignment_status: "ACTIVE" },
  trainer_panel: null,
});
assert.ok(dom.elements["decision-outcome-counts"].textContent.includes("4892"));
assert.ok(dom.elements["decision-outcome-counts"].textContent.includes("TRADE_PROPOSAL: 1"));
assert.ok(
  dom.elements["agent-active-badge"].innerHTML.includes("ACTIVE STATIC TRADER"),
  "an ACTIVE assignment must show the ACTIVE STATIC TRADER badge"
);

renderTrainerPanel({
  decision_outcome_counts: {},
  agent_panel: null,
  trainer_panel: null,
});
assert.ok(
  !dom.elements["agent-active-badge"].innerHTML.includes("ACTIVE STATIC TRADER"),
  "no assignment must not show the ACTIVE STATIC TRADER badge"
);

// --- Trainer campaign/dataset/candidate panels rebuild from trainer_panel -
const firstState = {
  decision_outcome_counts: {},
  agent_panel: null,
  trainer_panel: {
    campaign: {
      reachability: "REACHABLE",
      campaign_id: "CAM-FULLRUN1-1",
      campaign_status: "FOUND",
      campaign_mode: "MODE_2",
      campaign_strategy_key: "ict-sb-eurusd-pivot2@v1",
      checked_at_utc: "2026-09-17T00:00:00Z",
      run_id: "FULLRUN1-1",
    },
    dataset: {
      discovered_count: 3,
      eligible_count: 0,
      excluded_count: 3,
      excluded_reasons: ["abc: never reached execution"],
      posted_to_trainer: false,
      checked_at_utc: "2026-09-17T00:00:00Z",
    },
    candidate: {
      availability: "NONE",
      research_status: null,
      candidate_strategy_spec_hash: null,
      parent_strategy_key: null,
    },
    verification: { result: "NOT_RUN", candidate_hash: null, verified_candidate_strategy_spec_hash: null },
    run_coherence: "COHERENT",
    run_coherence_detail: null,
  },
};
renderTrainerPanel(firstState);
let campaignHtml = dom.elements["trainer-campaign-body"].innerHTML;
assert.ok(campaignHtml.includes("CAM-FULLRUN1-1"));
assert.ok(campaignHtml.includes("REACHABLE"));
let datasetHtml = dom.elements["trainer-dataset-body"].innerHTML;
assert.ok(datasetHtml.includes("never reached execution"));
let candidateHtml = dom.elements["trainer-candidate-body"].innerHTML;
assert.ok(candidateHtml.includes("NOT_RUN"));
assert.equal(
  dom.elements["run-coherence-card"].style.display,
  "none",
  "a COHERENT run must not show the mismatch banner"
);

// A second, different poll must visibly replace the first render.
renderTrainerPanel({
  decision_outcome_counts: {},
  agent_panel: null,
  trainer_panel: {
    campaign: {
      reachability: "UNREACHABLE",
      campaign_id: "CAM-FULLRUN1-1",
      campaign_status: "NOT_FOUND",
      campaign_mode: null,
      campaign_strategy_key: null,
      checked_at_utc: "2026-09-17T00:05:00Z",
      run_id: "FULLRUN1-1",
    },
    dataset: {
      discovered_count: null,
      eligible_count: null,
      excluded_count: null,
      excluded_reasons: [],
      posted_to_trainer: null,
      checked_at_utc: null,
    },
    candidate: {
      availability: "AVAILABLE",
      research_status: "RESEARCH_PROMISING",
      candidate_strategy_spec_hash: "abcdef0123456789abcdef",
      parent_strategy_key: "ict-sb-eurusd-pivot2@v1",
    },
    verification: {
      result: "PASS",
      candidate_hash: "fedcba9876543210fedcba",
      verified_candidate_strategy_spec_hash: "abcdef0123456789abcdef",
    },
    run_coherence: "INCOHERENT",
    run_coherence_detail: "disagreeing run_id values: FULLRUN1-1, FULLRUN1-2",
  },
});
campaignHtml = dom.elements["trainer-campaign-body"].innerHTML;
assert.ok(campaignHtml.includes("UNREACHABLE"));
assert.ok(!campaignHtml.includes("REACHABLE>"), "must not retain the old REACHABLE value verbatim");
candidateHtml = dom.elements["trainer-candidate-body"].innerHTML;
assert.ok(candidateHtml.includes("RESEARCH_PROMISING"));
assert.ok(candidateHtml.includes("PASS"));
assert.equal(
  dom.elements["run-coherence-card"].style.display,
  "",
  "an INCOHERENT run must show the mismatch banner"
);
assert.ok(dom.elements["run-coherence-detail"].textContent.includes("FULLRUN1-1"));
assert.ok(dom.elements["run-coherence-detail"].textContent.includes("FULLRUN1-2"));

// --- Execution activity panel ---------------------------------------------
renderExecutionActivity({
  execution_activity: {
    requests_claimed_count: 1,
    event_counts_by_type: { REQUEST_CLAIMED: 1, ORDER_CHECKED: 1 },
    latest_event: {
      event_type: "ORDER_CHECKED",
      occurred_at_utc: "2026-09-17T00:10:00Z",
      order_request_id: "abc-123",
      detail: null,
    },
  },
});
let activityHtml = dom.elements["execution-activity-body"].innerHTML;
assert.ok(activityHtml.includes("REQUEST_CLAIMED: 1"));
assert.ok(activityHtml.includes("ORDER_CHECKED at 2026-09-17T00:10:00Z"));

renderExecutionActivity({
  execution_activity: { requests_claimed_count: 0, event_counts_by_type: {}, latest_event: null },
});
activityHtml = dom.elements["execution-activity-body"].innerHTML;
assert.ok(activityHtml.includes("NO EVIDENCE"));
assert.ok(!activityHtml.includes("REQUEST_CLAIMED"), "must not retain the previous poll's event counts");

console.log("dashboard_trainer_panel_test.mjs: all assertions passed");
