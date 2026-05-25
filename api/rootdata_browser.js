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

function rootDataUrlVariants(value) {
  const parsed = new URL(value);
  const originalHost = parsed.hostname || "www.rootdata.com";
  const hosts = [originalHost, "cn.rootdata.com", "www.rootdata.com"];
  const lowerPath = parsed.pathname.replace(/^\/projects\/detail\//i, "/projects/detail/");
  const upperPath = parsed.pathname.replace(/^\/projects\/detail\//i, "/Projects/detail/");
  const variants = [];
  for (const host of hosts) {
    for (const pathname of [lowerPath, upperPath]) {
      const candidate = new URL(parsed.toString());
      candidate.protocol = candidate.protocol || "https:";
      candidate.hostname = host;
      candidate.pathname = pathname;
      const asString = candidate.toString();
      if (!variants.includes(asString)) {
        variants.push(asString);
      }
    }
  }
  return variants;
}

function htmlLooksLikeRootDataDetail(html) {
  if (!html || !/<h1[^>]*>[^<]+<\/h1>/i.test(html)) {
    return false;
  }
  return /self\.__next_f|__NEXT_DATA__|milestones|facAmount|hapDate|twitterUrl|yingUrl|team\\?":/i.test(html);
}

async function renderRootDataDetail(page, targetUrl) {
  let bestHtml = "";
  for (const url of rootDataUrlVariants(targetUrl)) {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
    for (let attempt = 0; attempt < 7; attempt += 1) {
      const html = await page.content();
      if (html.length > bestHtml.length) {
        bestHtml = html;
      }
      if (htmlLooksLikeRootDataDetail(html)) {
        return html;
      }
      await page.waitForTimeout(1000);
    }
  }
  return bestHtml;
}

async function rootDataBrowserHandler(req, res) {
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
    const html = await renderRootDataDetail(page, targetUrl);
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
}

module.exports = rootDataBrowserHandler;
module.exports._private = {
  htmlLooksLikeRootDataDetail,
  rootDataUrlVariants,
};
