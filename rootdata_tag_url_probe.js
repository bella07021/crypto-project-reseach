#!/usr/bin/env node

const { chromium } = require("playwright-core");

async function main() {
  const context = await chromium.launchPersistentContext(".rootdata-chrome-profile", {
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
    args: ["--disable-blink-features=AutomationControlled"],
  });

  const page = context.pages()[0] || (await context.newPage());
  await page.goto("https://cn.rootdata.com/Projects", {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  await page.waitForTimeout(4000);

  for (const label of ["基础设施", "DeFi", "NFT", "游戏", "CeFi", "DAO", "工具", "数据&分析", "社交"]) {
    await page.goto("https://cn.rootdata.com/Projects", {
      waitUntil: "domcontentloaded",
      timeout: 120000,
    });
    await page.waitForTimeout(3000);
    await page.getByText(label, { exact: true }).first().click();
    await page.waitForTimeout(3000);
    console.log(JSON.stringify({ label, url: page.url() }));
  }

  await context.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
