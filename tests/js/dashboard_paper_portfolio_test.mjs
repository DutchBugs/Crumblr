// Regression test: the browser's renderPaperPortfolio() must actually
// rebuild the paper-portfolio-account-body/paper-portfolio-positions-body
// containers from state.paper_portfolio on every poll -- the same
// "never refreshed on poll" class of bug already fixed for the broker
// panel and the activity feed, checked here proactively before it could
// ship, and must render each of the three statuses (OK/DEGRADED/NO
// EVIDENCE) distinctly rather than collapsing them.
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
  const elements = {
    "paper-portfolio-account-body": makeElement(),
    "paper-portfolio-positions-body": makeElement(),
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

const { renderPaperPortfolio } = moduleShim.exports;
assert.equal(typeof renderPaperPortfolio, "function", "renderPaperPortfolio was not exported for testing");

// --- NO EVIDENCE ------------------------------------------------------
renderPaperPortfolio({
  paper_portfolio: { status: "NO EVIDENCE", detail: "no PAPER_LITE journal has been written yet", portfolio: null, positions: [] },
});
let account = dom.elements["paper-portfolio-account-body"].innerHTML;
let positions = dom.elements["paper-portfolio-positions-body"].innerHTML;
assert.ok(account.includes("NO EVIDENCE"), "account panel should show NO EVIDENCE");
assert.ok(account.includes("no PAPER_LITE journal has been written yet"), "account panel should show the detail");
assert.ok(!account.includes("empty bad"), "NO EVIDENCE must not render with the bad/alarming class");
assert.ok(positions.includes("NO EVIDENCE"), "positions panel should also show NO EVIDENCE");

// --- DEGRADED -----------------------------------------------------------
renderPaperPortfolio({
  paper_portfolio: { status: "DEGRADED", detail: "PAPER_LITE journal could not be replayed: corrupt", portfolio: null, positions: [] },
});
account = dom.elements["paper-portfolio-account-body"].innerHTML;
assert.ok(account.includes("empty bad"), "DEGRADED must render with the bad/alarming class");
assert.ok(account.includes("corrupt"), "DEGRADED should surface the real detail");

// --- OK, with real numbers and a real open position ---------------------
renderPaperPortfolio({
  paper_portfolio: {
    status: "OK",
    detail: null,
    portfolio: {
      balance: "10000",
      equity: "10050",
      unrealised_profit: "50",
      realized_profit: "0",
      open_position_count: 1,
      closed_trade_count: 0,
      authorized_open_risk_amount: "100",
      exact_open_risk_amount: "95",
      exact_open_risk_fraction: "0.0095",
      latest_observation_time_utc: "2026-09-07T12:00:00+00:00",
    },
    positions: [
      {
        ticket: 1,
        broker_symbol: "EURUSD",
        side: "BUY",
        volume: "0.10",
        open_price: "1.08500",
        current_price: "1.08600",
        stop_loss_price: "1.08300",
        take_profit_price: "1.08900",
        profit: "50",
        swap: "0",
      },
    ],
  },
});
account = dom.elements["paper-portfolio-account-body"].innerHTML;
positions = dom.elements["paper-portfolio-positions-body"].innerHTML;
assert.ok(account.includes("10000"), "account panel should show the real balance");
assert.ok(account.includes("10050"), "account panel should show the real equity");
assert.ok(account.includes("0.0095"), "account panel should show the exact open risk fraction");
assert.ok(positions.includes("EURUSD"), "positions panel should show the real open position");
assert.ok(positions.includes("1.08500"), "positions panel should show the real open price");

// --- A second, different poll must visibly replace the first, not merely
// append to it or leave stale numbers on screen -------------------------
renderPaperPortfolio({
  paper_portfolio: {
    status: "OK",
    detail: null,
    portfolio: {
      balance: "10000",
      equity: "10000",
      unrealised_profit: "0",
      realized_profit: "0",
      open_position_count: 0,
      closed_trade_count: 1,
      authorized_open_risk_amount: "0",
      exact_open_risk_amount: null,
      exact_open_risk_fraction: null,
      latest_observation_time_utc: "2026-09-07T12:05:00+00:00",
    },
    positions: [],
  },
});
positions = dom.elements["paper-portfolio-positions-body"].innerHTML;
assert.ok(positions.includes("0 open simulated positions"), "second poll with no positions should show the empty state");
assert.ok(!positions.includes("EURUSD"), "second poll must not retain the previous poll's closed position");

console.log("dashboard_paper_portfolio_test.mjs: all assertions passed");
