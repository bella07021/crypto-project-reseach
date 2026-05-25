const { chromium } = require("playwright-core");
const serverlessChromium = require("@sparticuz/chromium");

const USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

function isAllowedRootDataUrl(value) {
  try {
    const parsed = new URL(value);
    return ["cn.rootdata.com", "www.rootdata.com", "rootdata.com"].includes(parsed.hostname);
  } catch {
    return false;
  }
}

module.exports = async (req, res) => {
  const targetUrl = req.query.url;
  if (!targetUrl || !isAllowedRootDataUrl(targetUrl)) {
    res.statusCode = 400;
    res.setHeader("content-type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ ok: false, error: "invalid RootData url" }));
    return;
  }

  let browser;
  try {
    browser = await chromium.launch({
      executablePath: await serverlessChromium.executablePath(),
      headless: true,
      args: serverlessChromium.args,
    });
    const page = await browser.newPage({ userAgent: USER_AGENT, locale: "en-US" });
    await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(5000);
    const html = await page.content();
    res.statusCode = 200;
    res.setHeader("content-type", "text/html; charset=utf-8");
    res.end(html);
  } catch (error) {
    res.statusCode = 500;
    res.setHeader("content-type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ ok: false, error: String(error && error.message ? error.message : error) }));
  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }
  }
};
