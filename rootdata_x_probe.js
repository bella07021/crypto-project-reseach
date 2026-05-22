#!/usr/bin/env node

const { chromium } = require("playwright-core");

const url = process.argv[2];
if (!url) {
  console.error("Usage: node rootdata_x_probe.js <project_url>");
  process.exit(1);
}

async function main() {
  const context = await chromium.launchPersistentContext(".rootdata-chrome-profile", {
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
    args: ["--disable-blink-features=AutomationControlled"],
  });
  const page = context.pages()[0] || (await context.newPage());
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForTimeout(1500);
  const data = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('a[href*="x.com"], a[href*="twitter.com"]')).map((a) => {
      const r = a.getBoundingClientRect();
      return {
        href: a.href,
        text: (a.innerText || a.textContent || "").trim(),
        top: r.top,
        left: r.left,
        width: r.width,
        height: r.height,
        cls: a.className,
        parent: a.parentElement ? a.parentElement.className : "",
      };
    });
  });
  console.log(JSON.stringify(data, null, 2));
  await context.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
