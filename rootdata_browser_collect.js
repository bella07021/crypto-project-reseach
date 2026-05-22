#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright-core");

const CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PROFILE_DIR = path.join(process.cwd(), ".rootdata-chrome-profile");
const OUTPUT_DIR = path.join(process.cwd(), "output");
const TEST_URL = "https://cn.rootdata.com/Projects?sd=309&st=1";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function ensureReadyPage(page) {
  const timeoutMs = 15 * 60 * 1000;
  const start = Date.now();

  while (Date.now() - start < timeoutMs) {
    const state = await page.evaluate(() => {
      const text = document.body ? document.body.innerText : "";
      const hasCaptcha = text.includes("captcha") || text.includes("验证") || !!document.querySelector("#CaptchaScript");
      const detailLinks = Array.from(document.querySelectorAll('a[href*="/Projects/detail/"]')).length;
      return {
        hasCaptcha,
        detailLinks,
        title: document.title,
        textPreview: text.slice(0, 300),
      };
    });

    console.log(
      JSON.stringify({
        type: "page_state",
        title: state.title,
        hasCaptcha: state.hasCaptcha,
        detailLinks: state.detailLinks,
      })
    );

    if (!state.hasCaptcha && state.detailLinks > 0) {
      return;
    }
    await sleep(3000);
  }

  throw new Error("Timed out waiting for RootData page to become scrapeable.");
}

async function scrapeVisibleProjects(page) {
  return page.evaluate(() => {
    const anchors = Array.from(document.querySelectorAll('a[href*="/Projects/detail/"]'));
    const seen = new Set();
    const rows = [];

    for (const anchor of anchors) {
      const href = anchor.href;
      const name = (anchor.textContent || "").trim();
      if (!href || !name || seen.has(href)) continue;

      const container = anchor.closest("tr, li, div") || anchor.parentElement;
      const text = (container?.innerText || "").replace(/\s+/g, " ").trim();
      if (!text) continue;

      seen.add(href);
      rows.push({
        project_name: name,
        project_url: href,
        raw_text: text,
      });
    }

    return rows;
  });
}

async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    executablePath: CHROME_PATH,
    headless: false,
    viewport: { width: 1440, height: 960 },
    args: ["--disable-blink-features=AutomationControlled"],
  });

  const page = context.pages()[0] || (await context.newPage());
  await page.goto(TEST_URL, { waitUntil: "domcontentloaded", timeout: 120000 });

  console.log("Chrome opened on RootData. Please solve any captcha/login in the browser window.");
  await ensureReadyPage(page);
  await page.waitForTimeout(5000);

  const rows = await scrapeVisibleProjects(page);
  const outputPath = path.join(OUTPUT_DIR, "rootdata_test_visible_projects.json");
  fs.writeFileSync(outputPath, JSON.stringify(rows, null, 2), "utf-8");

  console.log(JSON.stringify({ type: "result", outputPath, count: rows.length }, null, 2));
  console.log("Keeping browser open for 60 seconds so you can inspect the page.");
  await sleep(60000);

  await context.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
