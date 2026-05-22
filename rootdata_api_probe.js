#!/usr/bin/env node

const { chromium } = require("playwright-core");

async function main() {
  const context = await chromium.launchPersistentContext(".rootdata-chrome-profile", {
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
    args: ["--disable-blink-features=AutomationControlled"],
  });

  const page = context.pages()[0] || (await context.newPage());

  page.on("response", async (response) => {
    const url = response.url();
    if (!url.includes("/pc/data/sc_item_list_page")) return;

    const request = response.request();
    let body = null;
    let json = null;
    try {
      body = request.postData();
    } catch (error) {}
    try {
      json = await response.json();
    } catch (error) {}

    console.log(JSON.stringify({
      url,
      status: response.status(),
      method: request.method(),
      postData: body,
      responseSample: json,
    }, null, 2));
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
