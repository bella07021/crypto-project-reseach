#!/usr/bin/env node

const { chromium } = require("playwright-core");
const https = require("https");

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

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const request = https.get(
      url,
      {
        headers: {
          "User-Agent": "Mozilla/5.0",
          "Accept": "application/json",
        },
      },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          body += chunk;
        });
        response.on("end", () => {
          if (response.statusCode < 200 || response.statusCode >= 300) {
            reject(new Error(`CMC data-api returned HTTP ${response.statusCode}`));
            return;
          }
          try {
            resolve(JSON.parse(body));
          } catch (error) {
            reject(error);
          }
        });
      },
    );
    request.setTimeout(15000, () => {
      request.destroy(new Error("CMC data-api request timed out"));
    });
    request.on("error", reject);
  });
}

function mapApiPair(pair) {
  const exchangeName = pair.exchangeName || pair.exchange?.name || "";
  return {
    exchange: {
      name: exchangeName,
      slug: pair.exchangeSlug || exchangeName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""),
    },
    market_pair: pair.marketPair || pair.market_pair || "",
    category: String(pair.category || "").toLowerCase(),
    source: "CoinMarketCap Data API",
    expected_symbol: symbol,
  };
}

async function fetchDataApiPairs() {
  const allPairs = [];
  const limit = 100;
  let expectedTotal = 0;
  for (let start = 1; start <= 201; start += limit) {
    const params = new URLSearchParams({
      slug,
      start: String(start),
      limit: String(limit),
      category: "all",
    });
    const payload = await fetchJson(`https://api.coinmarketcap.com/data-api/v3/cryptocurrency/market-pairs/latest?${params}`);
    const data = payload.data || {};
    const pairs = data.marketPairs || data.market_pairs || [];
    expectedTotal = Number(data.numMarketPairs || data.num_market_pairs || expectedTotal || 0);
    allPairs.push(...pairs.map(mapApiPair));
    if (pairs.length < limit || (expectedTotal && allPairs.length >= expectedTotal)) {
      break;
    }
  }
  return allPairs;
}

async function main() {
  try {
    const apiPairs = await fetchDataApiPairs();
    if (apiPairs.length) {
      console.log(JSON.stringify({ ok: true, pairs: apiPairs }, null, 2));
      return;
    }
  } catch (error) {
    // Fall back to browser scraping below. The Python caller only reads stdout.
  }

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
