// Regression test: the browser's renderPipeline()/pipelineStageClass() must
// (1) actually rebuild the pipeline-body container from state.pipeline on
// every poll -- the pre-restaging "Decision pipeline" section was rendered
// once server-side and never touched again by applyState(), the same class
// of gap already fixed for the broker/activity panels -- and (2) classify
// every stage value exactly the way dashboard/pipeline.py::pipeline_stage_class
// does.
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

function makeElement() {
  return { innerHTML: "" };
}

function makeDom() {
  const elements = { "pipeline-body": makeElement() };
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

const { renderPipeline, pipelineStageClass } = moduleShim.exports;
assert.equal(typeof renderPipeline, "function", "renderPipeline was not exported for testing");
assert.equal(typeof pipelineStageClass, "function", "pipelineStageClass was not exported for testing");

// --- pipelineStageClass parity with dashboard/pipeline.py's three sets ---
const GOOD = ["OBSERVED", "ISSUED", "ACCEPTED", "TRADE_PROPOSAL", "PASS", "APPROVE"];
const WARN = ["AWAITING_OUTCOME", "SKIPPED_PAPER_MODE", "CLAIMED"];
const BAD = ["REJECTED", "BLOCK", "PAPER_ORDER_CHECK_BLOCKED", "UNKNOWN"];
const NEUTRAL = ["NO_TRADE", "NOT REACHED", "N/A", "RESPONSE RECEIVED"];

for (const value of GOOD) assert.equal(pipelineStageClass(value), "good", value + " should be good");
for (const value of WARN) assert.equal(pipelineStageClass(value), "warn", value + " should be warn");
for (const value of BAD) assert.equal(pipelineStageClass(value), "bad", value + " should be bad");
for (const value of NEUTRAL) assert.equal(pipelineStageClass(value), "neutral", value + " should be neutral");
assert.equal(pipelineStageClass("SOME_FUTURE_VALUE"), "neutral", "unmapped values fall back to neutral");
assert.equal(pipelineStageClass("accepted"), "good", "classification is case-insensitive");

// --- renderPipeline actually rebuilds the container from state.pipeline --
renderPipeline({
  pipeline: {
    market: "OBSERVED",
    context: "ISSUED",
    agent: "TRADE_PROPOSAL",
    gateway: "ACCEPTED",
    risk: "BLOCK",
    policy: "NOT REACHED",
    supervisor: "NOT REACHED",
    paper: "NOT REACHED",
  },
});
let html1 = dom.elements["pipeline-body"].innerHTML;
assert.ok(html1.includes("TRADE_PROPOSAL"), "first render should show the first poll's agent stage");
assert.ok(html1.includes("BLOCK"), "first render should show the first poll's risk stage");

// A second, different poll response must visibly replace the first render,
// not merely append to it or leave the old stage values on screen.
renderPipeline({
  pipeline: {
    market: "OBSERVED",
    context: "ISSUED",
    agent: "NO_TRADE",
    gateway: "ACCEPTED",
    risk: "N/A",
    policy: "N/A",
    supervisor: "N/A",
    paper: "N/A",
  },
});
let html2 = dom.elements["pipeline-body"].innerHTML;
assert.ok(html2.includes("NO_TRADE"), "second render should show the new poll's agent stage");
assert.ok(!html2.includes("TRADE_PROPOSAL"), "second render must not retain the previous poll's agent stage");
assert.ok(!html2.includes("BLOCK"), "second render must not retain the previous poll's risk stage");

console.log("dashboard_pipeline_test.mjs: all assertions passed");
