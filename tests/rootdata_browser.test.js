const test = require("node:test");
const assert = require("node:assert/strict");

const { _private } = require("../api/rootdata_browser.js");

test("rootDataUrlVariants keeps original first and tries cn/www path variants", () => {
  const variants = _private.rootDataUrlVariants(
    "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
  );

  assert.equal(variants[0], "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D");
  assert.ok(variants.includes("https://cn.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D"));
  assert.ok(variants.includes("https://www.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D"));
  assert.ok(variants.includes("https://www.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D"));
  assert.equal(new Set(variants).size, variants.length);
});

test("htmlLooksLikeRootDataDetail waits for rendered detail markers", () => {
  assert.equal(
    _private.htmlLooksLikeRootDataDetail("<html><body>Please enable JavaScript</body></html>"),
    false,
  );
  assert.equal(
    _private.htmlLooksLikeRootDataDetail(
      '<h1>Nexus</h1><script>self.__next_f.push([1,"\\"milestones\\":[{\\"facAmountUs\\":25000000}]"])</script>',
    ),
    true,
  );
});
