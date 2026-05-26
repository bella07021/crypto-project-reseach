#!/usr/bin/env node

const { chromium } = require("playwright-core");

const urls = process.argv.slice(2);

if (!urls.length) {
  console.error("Usage: node x_signal_scrape.js <x-url> [x-url...]");
  process.exit(1);
}

async function main() {
  const context = await chromium.launchPersistentContext(".x-chrome-profile", {
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: process.env.X_BROWSER_HEADLESS !== "0",
    args: ["--disable-blink-features=AutomationControlled"],
    locale: "en-US",
    userAgent:
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  });
  const page = await context.newPage();
  const outputs = [];
  for (const url of urls) {
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
      await page.waitForTimeout(Number(process.env.X_BROWSER_WAIT_MS || 7000));
      const payload = await page.evaluate(() => {
        const links = Array.from(document.querySelectorAll('a[href*="/status/"]'))
          .map((anchor) => anchor.href)
          .filter(Boolean);
        return {
          text: document.body ? document.body.innerText : "",
          links,
        };
      });
      outputs.push(`URL: ${url}\n${payload.text}\n${payload.links.join("\n")}`);
    } catch (error) {
      outputs.push(`URL: ${url}\nERROR: ${error && error.message ? error.message : String(error)}`);
    }
  }
  console.log(outputs.join("\n\n---X-SIGNAL-PAGE---\n\n"));
  await context.close();
}

main().catch((error) => {
  console.error(String(error && error.message ? error.message : error));
  process.exit(1);
});
