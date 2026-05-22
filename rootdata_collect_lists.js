#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright-core");

const OUTPUT_DIR = path.join(process.cwd(), "output");
const PROFILE_DIR = ".rootdata-chrome-profile";
const CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const CATEGORY_FILTER = process.env.CATEGORY_FILTER || "";
const MAX_ITEMS = Number(process.env.MAX_ITEMS || "500");

const CATEGORY_CONFIG = [
  { bucket: "infra", rootdata_tag: "基础设施", url: "https://cn.rootdata.com/Projects?sd=228&st=1" },
  { bucket: "defi", rootdata_tag: "DeFi", url: "https://cn.rootdata.com/Projects?sd=224&st=1" },
  { bucket: "nft", rootdata_tag: "NFT", url: "https://cn.rootdata.com/Projects?sd=225&st=1" },
  { bucket: "gamefi", rootdata_tag: "游戏", url: "https://cn.rootdata.com/Projects?sd=111&st=1" },
  { bucket: "cefi", rootdata_tag: "CeFi", url: "https://cn.rootdata.com/Projects?sd=226&st=1" },
  { bucket: "dao", rootdata_tag: "DAO", url: "https://cn.rootdata.com/Projects?sd=227&st=1" },
  { bucket: "tools&information", rootdata_tag: "工具", url: "https://cn.rootdata.com/Projects?sd=150&st=1" },
  { bucket: "tools&information", rootdata_tag: "数据&分析", url: "https://cn.rootdata.com/Projects?sd=152&st=1" },
  { bucket: "social&entertainment", rootdata_tag: "社交", url: "https://cn.rootdata.com/Projects?sd=140&st=1" },
  { bucket: "social&entertainment", rootdata_tag: "创作者经济", url: "https://cn.rootdata.com/Projects?sd=168&st=1" },
];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function csvEscape(value) {
  const text = value == null ? "" : String(value);
  if (/[",\n]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

async function ensurePageReady(page) {
  const start = Date.now();
  while (Date.now() - start < 120000) {
    const state = await page.evaluate(() => {
      const rows = document.querySelectorAll("tbody tr").length;
      const captcha = !!document.querySelector("#CaptchaScript");
      return { rows, captcha, title: document.title };
    });
    if (!state.captcha && state.rows > 0) return state.rows;
    await sleep(2000);
  }
  throw new Error(`Timed out waiting for rows on ${page.url()}`);
}

async function scrapeCurrentRows(page, bucket, rootdataTag, offset) {
  const rows = await page.evaluate(({ bucket, rootdataTag, offset }) => {
    const trs = Array.from(document.querySelectorAll("tbody tr"));
    return trs.map((tr, index) => {
      const tds = Array.from(tr.querySelectorAll("td"));
      const detailLink = tr.querySelector('a[href*="/Projects/detail/"]');
      const nameLink = tr.querySelector('a.list_name[href*="/Projects/detail/"]') || detailLink;
      const name = (nameLink?.textContent || detailLink?.getAttribute("alt") || "").trim();
      const token = tds[0] ? (tds[0].innerText || "").replace(name, "").trim().split(/\s+/).join(" ") : "";
      const tags = tds[1] ? (tds[1].innerText || "").replace(/\s+/g, " ").trim() : "";
      const ecosystem = tds[2] ? (tds[2].innerText || "").replace(/\s+/g, " ").trim() : "";
      const description = tds[3] ? (tds[3].innerText || "").replace(/\s+/g, " ").trim() : "";
      const growth = tds[4] ? (tds[4].innerText || "").replace(/\s+/g, " ").trim() : "";
      const heat = tds[5] ? (tds[5].innerText || "").replace(/\s+/g, " ").trim() : "";
      return {
        bucket,
        rootdata_tag: rootdataTag,
        bucket_rank: offset + index + 1,
        project_name: name,
        token_symbol: token,
        rootdata_subtags: tags,
        ecosystem,
        description,
        rd_growth_index: growth,
        rd_heat_index: heat,
        project_url: detailLink ? detailLink.href : "",
      };
    }).filter((row) => row.project_name && row.project_url);
  }, { bucket, rootdataTag, offset });
  return rows;
}

async function collectCategory(page, config, maxItems = 500) {
  const allRows = [];
  const seen = new Set();
  let pageNum = 1;
  const maxPages = Math.ceil(maxItems / 30) + 2;

  await page.goto(config.url, { waitUntil: "domcontentloaded", timeout: 120000 });
  await ensurePageReady(page);
  await page.waitForTimeout(2500);

  while (allRows.length < maxItems && pageNum <= maxPages) {
    const rows = await scrapeCurrentRows(page, config.bucket, config.rootdata_tag, allRows.length);
    const freshRows = rows.filter((row) => {
      if (seen.has(row.project_url)) return false;
      seen.add(row.project_url);
      return true;
    });

    if (freshRows.length === 0) break;
    allRows.push(...freshRows);
    console.log(JSON.stringify({
      bucket: config.bucket,
      rootdata_tag: config.rootdata_tag,
      page: pageNum,
      page_rows: freshRows.length,
      total_collected: allRows.length,
    }));

    if (freshRows.length < 30) break;
    if (allRows.length >= maxItems) break;

    const nextPage = pageNum + 1;
    const firstProject = freshRows[0]?.project_url || "";
    const clicked = await page.evaluate(({ nextPage }) => {
      const candidates = Array.from(document.querySelectorAll(".number"));
      const target = candidates.find((el) => (el.textContent || "").trim() === String(nextPage));
      if (!target) return false;
      target.click();
      return true;
    }, { nextPage });
    if (!clicked) break;

    await page.waitForFunction(
      (prevUrl) => {
        const firstLink = document.querySelector('tbody tr a[href*="/Projects/detail/"]');
        return firstLink && firstLink.href !== prevUrl;
      },
      firstProject,
      { timeout: 120000 }
    );
    await page.waitForTimeout(2500);
    pageNum += 1;
  }

  return allRows.slice(0, maxItems);
}

async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    executablePath: CHROME_PATH,
    headless: true,
    args: ["--disable-blink-features=AutomationControlled"],
  });

  const page = context.pages()[0] || (await context.newPage());
  const results = [];

  const activeConfigs = CATEGORY_FILTER
    ? CATEGORY_CONFIG.filter((config) => config.bucket === CATEGORY_FILTER || config.rootdata_tag === CATEGORY_FILTER)
    : CATEGORY_CONFIG;

  for (const config of activeConfigs) {
    const rows = await collectCategory(page, config, MAX_ITEMS);
    results.push(...rows);
  }

  const jsonPath = path.join(OUTPUT_DIR, "rootdata_category_project_lists.json");
  fs.writeFileSync(jsonPath, JSON.stringify(results, null, 2), "utf-8");

  const csvPath = path.join(OUTPUT_DIR, "rootdata_category_project_lists.csv");
  const headers = [
    "bucket",
    "rootdata_tag",
    "bucket_rank",
    "project_name",
    "token_symbol",
    "rootdata_subtags",
    "ecosystem",
    "description",
    "rd_growth_index",
    "rd_heat_index",
    "project_url",
  ];
  const lines = [headers.join(",")];
  for (const row of results) {
    lines.push(headers.map((key) => csvEscape(row[key])).join(","));
  }
  fs.writeFileSync(csvPath, `${lines.join("\n")}\n`, "utf-8");

  console.log(JSON.stringify({ jsonPath, csvPath, count: results.length }, null, 2));
  await context.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
