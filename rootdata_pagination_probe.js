#!/usr/bin/env node

const { chromium } = require("playwright-core");

async function main() {
  const context = await chromium.launchPersistentContext(".rootdata-chrome-profile", {
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
    args: ["--disable-blink-features=AutomationControlled"],
  });

  const page = context.pages()[0] || (await context.newPage());
  await page.goto("https://cn.rootdata.com/Projects?sd=228&st=1", {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  await page.waitForTimeout(5000);

  const data = await page.evaluate(() => {
    const links = Array.from(document.querySelectorAll("a")).map((a) => ({
      text: (a.innerText || "").trim(),
      href: a.href,
      cls: a.className,
    }));
    const buttons = Array.from(document.querySelectorAll("button, li, span, div")).map((el) => ({
      text: (el.innerText || "").trim(),
      cls: el.className,
      role: el.getAttribute("role"),
    })).filter((x) => /(^|\s)(1|2|3|4|5|6|下一页|next)(\s|$)/.test(x.text));
    const body = document.body ? document.body.innerText.slice(-1200) : "";
    return { links: links.filter((x) => x.text && /^\d+$/.test(x.text)).slice(0, 50), buttons: buttons.slice(0, 100), body };
  });

  console.log(JSON.stringify(data, null, 2));
  await context.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
