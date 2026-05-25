#!/usr/bin/env node

const { chromium } = require("playwright-core");

const slug = process.argv[2];
const symbol = (process.argv[3] || "").toUpperCase();

if (!slug) {
  console.error("Usage: node cmc_market_scrape.js <coinmarketcap-slug> [symbol]");
  process.exit(1);
}

async function launchContext() {
  if (process.env.VERCEL) {
    const serverlessChromium = require("@sparticuz/chromium");
    return chromium.launchPersistentContext("/tmp/cmc-chrome-profile", {
      executablePath: await serverlessChromium.executablePath(),
      headless: true,
      args: serverlessChromium.args,
    });
  }

  return chromium.launchPersistentContext(".cmc-chrome-profile", {
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
    args: ["--disable-blink-features=AutomationControlled"],
  });
}

async function main() {
  const context = await launchContext();
  const page = context.pages()[0] || (await context.newPage());
  await page.goto(`https://coinmarketcap.com/currencies/${slug}/#Markets`, {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });
  await page.waitForTimeout(7000);

  const data = await page.evaluate((expectedSymbol) => {
    const rows = Array.from(document.querySelectorAll("table tr"))
      .map((row) => Array.from(row.querySelectorAll("th,td")).map((cell) => cell.innerText.trim()).filter(Boolean))
      .filter((cells) => cells.length >= 3 && /^\d+$/.test(cells[0]));

    return rows.map((cells) => ({
      exchange: { name: cells[1], slug: cells[1].toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") },
      market_pair: cells[2],
      category: "spot",
      source: "CoinMarketCap Web",
      expected_symbol: expectedSymbol,
    }));
  }, symbol);

  console.log(JSON.stringify({ ok: true, pairs: data }, null, 2));
  await context.close();
}

main().catch(async (error) => {
  console.error(JSON.stringify({ ok: false, error: String(error && error.message ? error.message : error) }));
  process.exit(1);
});
