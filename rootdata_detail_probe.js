#!/usr/bin/env node

const { chromium } = require("playwright-core");

const targetUrl = process.argv[2];
if (!targetUrl) {
  console.error("Usage: node rootdata_detail_probe.js <rootdata_project_url>");
  process.exit(1);
}

async function main() {
  const context = await chromium.launchPersistentContext(".rootdata-chrome-profile", {
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
    args: ["--disable-blink-features=AutomationControlled"],
  });

  const page = context.pages()[0] || (await context.newPage());
  const responses = [];
  page.on("response", async (response) => {
    const url = response.url();
    if (!/pc\/data|api|detail|get_item|social|x\.com|twitter\.com/i.test(url)) return;
    if (responses.length >= 50) return;
    responses.push({
      url,
      status: response.status(),
      type: response.request().resourceType(),
      postData: response.request().postData() || "",
    });
  });

  await page.goto(targetUrl, { waitUntil: "networkidle", timeout: 120000 });
  await page.waitForTimeout(4000);

  const data = await page.evaluate(() => {
    const anchors = Array.from(document.querySelectorAll("a[href]")).map((a) => ({
      href: a.href,
      text: (a.innerText || a.textContent || "").trim(),
      cls: a.className,
      parent: a.parentElement ? a.parentElement.className : "",
    }));

    const scripts = Array.from(document.querySelectorAll("script"))
      .map((s) => s.textContent || "")
      .filter(Boolean)
      .slice(0, 20);

    const body = document.body ? document.body.innerText.slice(0, 4000) : "";
    const html = document.documentElement.outerHTML;
    const xMatches = html.match(/https?:\/\/(?:x|twitter)\.com\/[A-Za-z0-9_]+/g) || [];
    const websiteMatches = html.match(/https?:\/\/[A-Za-z0-9._~:/?#[\]@!$&'()*+,;=%-]+/g) || [];

    return {
      title: document.title,
      anchors: anchors.filter((a) => /x\.com|twitter\.com|linkedin|github|medium|discord|t\.me|http/i.test(a.href)),
      xMatches: [...new Set(xMatches)],
      websiteMatches: [...new Set(websiteMatches)].filter((u) => !/rootdata\.com|x\.com|twitter\.com/.test(u)).slice(0, 100),
      scripts,
      body,
      nuxtStateKeys: Object.keys(window).filter((k) => /__NUXT|NUXT|__INITIAL/i.test(k)),
    };
  });

  console.log(JSON.stringify({ page: data, responses }, null, 2));
  await context.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
