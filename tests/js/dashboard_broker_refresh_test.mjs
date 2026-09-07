// Regression test for dashboard.html's own poll-refresh JavaScript --
// specifically that a second /api/state payload with a *different* broker
// snapshot visibly replaces the account/positions/pending-orders panels,
// rather than the panels staying frozen at whatever the server rendered on
// first page load (the exact gap named in owner review of commit e19da39).
//
// Deliberately no test framework, no npm install, no package.json: this
// project's own testing guidance is "browser JS can be tested indirectly
// through rendered HTML/static contract... do not introduce a heavy
// frontend toolchain only for this." Node's built-in `vm` module lets this
// run the *actual* shipped <script> block from the real template file
// (not a reimplementation that could silently drift from production code)
// against a small hand-built DOM stub, using nothing beyond the Node
// runtime already present on this machine.
//
// Invoked from Python via tests/unit/test_dashboard_js_broker_refresh.py,
// which skips (does not fail) if `node` is not on PATH -- this test adds
// coverage where available, it does not make the whole gate depend on Node
// being installed everywhere this project's tests might run.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const templatePath = join(here, "..", "..", "src", "crumblr", "dashboard", "templates", "dashboard.html");
const html = readFileSync(templatePath, "utf-8");

// The template has exactly two <script> blocks: the JSON state bootstrap
// (`id="state-data"`) and the real logic block this test needs. Grab the
// *last* one specifically, rather than the first, so this does not
// silently start testing the JSON bootstrap block if the template's
// structure ever changes order.
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

const { renderBrokerPanels } = moduleShim.exports;
assert.equal(typeof renderBrokerPanels, "function", "renderBrokerPanels was not exported for testing");

function baseState(overrides) {
  return Object.assign(
    {
      generated_at_utc: "2026-09-07T12:00:05Z",
      broker: { account: null, positions: [], pending_orders: [] },
    },
    overrides
  );
}

// --- 1. No observation yet -----------------------------------------------
renderBrokerPanels(baseState({}));
assert.match(elements.get("broker-account-body").innerHTML, /No broker account observation recorded yet/);
assert.match(elements.get("broker-positions-body").innerHTML, /No broker account observation recorded yet/);
assert.match(elements.get("broker-pending-orders-body").innerHTML, /No broker account observation recorded yet/);

// --- 2. A real snapshot: account fields, one position, one pending order -
const snapshotOne = {
  observed_at_utc: "2026-09-07T12:00:00Z",
  server: "PepperstoneUK-Demo",
  account_ref: "fingerprint-one",
  currency: "EUR",
  leverage: 30,
  balance: "10000",
  equity: "10012.5",
  profit: "12.5",
  margin: "120",
  margin_free: "9892.5",
  margin_level: "8343.75",
  account_trade_allowed: true,
  terminal_trade_allowed: true,
  position_set_state: "COMPLETE",
  pending_order_set_state: "COMPLETE",
};
renderBrokerPanels(
  baseState({
    broker: {
      account: snapshotOne,
      positions: [
        {
          canonical_symbol: "EUR/USD",
          broker_symbol: "EURUSD",
          side: "BUY",
          volume: "0.05",
          open_price: "1.08512",
          current_price: "1.08600",
          stop_loss_price: "1.08012",
          take_profit_price: "1.09512",
          profit: "12.5",
        },
      ],
      pending_orders: [
        {
          canonical_symbol: "EUR/USD",
          broker_symbol: "EURUSD",
          order_type: "BUY_LIMIT",
          state: "PLACED",
          volume: "0.05",
          price: "1.08000",
          stop_loss_price: null,
          take_profit_price: null,
          expires_at_utc: null,
          order_id: 111,
        },
      ],
    },
  })
);
const accountHtml1 = elements.get("broker-account-body").innerHTML;
assert.match(accountHtml1, /fingerprint-one/);
assert.match(accountHtml1, />10000</);
assert.match(accountHtml1, /Account trade allowed/);
assert.match(accountHtml1, /Terminal trade allowed/);
const positionsHtml1 = elements.get("broker-positions-body").innerHTML;
assert.match(positionsHtml1, /1\.08512/);
const pendingHtml1 = elements.get("broker-pending-orders-body").innerHTML;
assert.match(pendingHtml1, />111</);

// --- 2b. account_trade_allowed and terminal_trade_allowed are two
// independent facts, visible as two separate rows after a poll refresh --
// not merged into one line, and a poll with account=true/terminal=false
// must show that exact mismatch (owner review of commit 1d0687e). -------
renderBrokerPanels(
  baseState({
    broker: {
      account: Object.assign({}, snapshotOne, {
        account_trade_allowed: true,
        terminal_trade_allowed: false,
      }),
      positions: [],
      pending_orders: [],
    },
  })
);
const mismatchHtml = elements.get("broker-account-body").innerHTML;
const accountRowIdx = mismatchHtml.indexOf("Account trade allowed");
const terminalRowIdx = mismatchHtml.indexOf("Terminal trade allowed");
assert.ok(accountRowIdx >= 0 && terminalRowIdx >= 0, "both trade-allowed rows must be present");
const accountRow = mismatchHtml.slice(accountRowIdx, accountRowIdx + 120);
const terminalRow = mismatchHtml.slice(terminalRowIdx, terminalRowIdx + 120);
assert.match(accountRow, />YES</, "account_trade_allowed=true must show YES on its own row");
assert.match(terminalRow, />NO</, "terminal_trade_allowed=false must show NO on its own row, not agree with account's YES");
assert.doesNotMatch(terminalRow, />YES</);

// --- 2c. terminal_trade_allowed=null must read as UNKNOWN, never YES -----
renderBrokerPanels(
  baseState({
    broker: {
      account: Object.assign({}, snapshotOne, {
        account_trade_allowed: true,
        terminal_trade_allowed: null,
      }),
      positions: [],
      pending_orders: [],
    },
  })
);
const nullTerminalHtml = elements.get("broker-account-body").innerHTML;
const nullTerminalIdx = nullTerminalHtml.indexOf("Terminal trade allowed");
const nullTerminalRow = nullTerminalHtml.slice(nullTerminalIdx, nullTerminalIdx + 120);
assert.match(nullTerminalRow, />UNKNOWN</, "a null terminal_trade_allowed must never be silently assumed YES");
assert.doesNotMatch(nullTerminalRow, />YES</);

// --- 3. A second, different snapshot must *replace*, not append ----------
const snapshotTwo = Object.assign({}, snapshotOne, {
  observed_at_utc: "2026-09-07T12:00:05Z",
  account_ref: "fingerprint-two",
  balance: "20000",
});
renderBrokerPanels(
  baseState({
    broker: { account: snapshotTwo, positions: [], pending_orders: [] },
  })
);
const accountHtml2 = elements.get("broker-account-body").innerHTML;
assert.match(accountHtml2, /fingerprint-two/);
assert.match(accountHtml2, />20000</);
assert.doesNotMatch(accountHtml2, /fingerprint-one/, "stale account_ref from the first snapshot leaked through");
assert.doesNotMatch(accountHtml2, />10000</, "stale balance from the first snapshot leaked through");

const positionsHtml2 = elements.get("broker-positions-body").innerHTML;
assert.match(positionsHtml2, /0 open positions \(complete broker snapshot\)/);
assert.doesNotMatch(positionsHtml2, /1\.08512/, "a position from the previous snapshot was not cleared");

const pendingHtml2 = elements.get("broker-pending-orders-body").innerHTML;
assert.match(pendingHtml2, /No pending orders observed/);
assert.doesNotMatch(pendingHtml2, />111</, "a pending order from the previous snapshot was not cleared");

// --- 4. COMPLETE + zero rows vs FAILED/UNKNOWN must render differently ---
renderBrokerPanels(
  baseState({
    broker: {
      account: Object.assign({}, snapshotOne, { position_set_state: "FAILED" }),
      positions: [],
      pending_orders: [],
    },
  })
);
const failedHtml = elements.get("broker-positions-body").innerHTML;
assert.match(failedHtml, /POSITION SET FAILED/);
assert.match(failedHtml, /absence cannot be trusted/);
assert.doesNotMatch(failedHtml, /0 open positions/, "FAILED must never read the same as confirmed-empty");

renderBrokerPanels(
  baseState({
    broker: {
      account: Object.assign({}, snapshotOne, { pending_order_set_state: "UNKNOWN" }),
      positions: [],
      pending_orders: [],
    },
  })
);
const unknownHtml = elements.get("broker-pending-orders-body").innerHTML;
assert.match(unknownHtml, /PENDING-ORDER SET UNKNOWN/);
assert.doesNotMatch(unknownHtml, /No pending orders observed/, "UNKNOWN must never read the same as confirmed-empty");

console.log("dashboard_broker_refresh_test.mjs: all assertions passed");
