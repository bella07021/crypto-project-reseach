#!/usr/bin/env node

const { chromium } = require("playwright-core");

const url = process.argv[2];

if (!url) {
  console.error("Usage: node rootdata_browser_scrape.js <rootdata-url>");
  process.exit(1);
}

async function launchBrowser() {
  if (process.env.VERCEL) {
    const serverlessChromium = require("@sparticuz/chromium");
    return chromium.launch({
      executablePath: await serverlessChromium.executablePath(),
      headless: true,
      args: serverlessChromium.args,
    });
  }

  return chromium.launch({
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
    args: ["--disable-blink-features=AutomationControlled"],
  });
}

async function main() {
  const browser = await launchBrowser();
  const page = await browser.newPage({
    userAgent:
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    locale: "en-US",
  });
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(5000);
  const html = await page.content();
  console.log(html);
  await browser.close();
}

main().catch((error) => {
  console.error(String(error && error.message ? error.message : error));
  process.exit(1);
});
