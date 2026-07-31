const test = require("node:test");
const assert = require("node:assert/strict");

const { _private } = require("../web/app.js");

test("scoreReason describes ordinary named investors instead of saying none", () => {
  assert.equal(
    _private.scoreReason("investor", {
      investors: ["MegaETH", "echo", "Flowdesk"],
      investor_highlights: [],
    }),
    "已识别 3 个投资方，未命中顶级/强机构",
  );
});

test("tgeSummary keeps an explicit TGE label when the date is unknown", () => {
  assert.equal(
    _private.tgeSummary({ tge_status: "已 TGE", tge_date: "" }),
    "已 TGE（日期待确认）",
  );
});
