#!/usr/bin/env node

const { chromium } = require("playwright-core");

async function main() {
  const context = await chromium.launchPersistentContext(".rootdata-chrome-profile", {
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
    args: ["--disable-blink-features=AutomationControlled"],
  });

  const page = context.pages()[0] || (await context.newPage());
  const seen = new Set();

  page.on("response", async (response) => {
    const url = response.url();
    if (seen.has(url)) return;
    if (!/api|graphql|nuxt|project|list|rank|heat|item/i.test(url)) return;
    seen.add(url);
    console.log(JSON.stringify({
      url,
      status: response.status(),
      type: response.request().resourceType(),
    }));
  });

  await page.goto("https://cn.rootdata.com/Projects?sd=228&st=1", {
    waitUntil: "networkidle",
    timeout: 120000,
  });
  await page.waitForTimeout(5000);
  await context.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
