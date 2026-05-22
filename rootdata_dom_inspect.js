#!/usr/bin/env node

const { chromium } = require("playwright-core");

async function main() {
  const context = await chromium.launchPersistentContext(".rootdata-chrome-profile", {
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
    args: ["--disable-blink-features=AutomationControlled"],
  });

  const page = context.pages()[0] || (await context.newPage());
  await page.goto("https://cn.rootdata.com/Projects?sd=309&st=1", {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  await page.waitForTimeout(5000);

  const data = await page.evaluate(() => {
    const anchor = document.querySelector('a[href*="/Projects/detail/"]');
    if (!anchor) return null;

    const chain = [];
    let node = anchor;
    for (let i = 0; node && i < 10; i += 1, node = node.parentElement) {
      chain.push({
        tag: node.tagName,
        className: node.className,
        text: (node.innerText || "").slice(0, 500),
        html: node.outerHTML.slice(0, 1200),
      });
    }
    return chain;
  });

  console.log(JSON.stringify(data, null, 2));
  await context.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
