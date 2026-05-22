#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright-core");

const INPUT_PATH = path.join(process.cwd(), "output", "rootdata_category_project_lists.json");
const OUTPUT_JSON = path.join(process.cwd(), "output", "rootdata_projects_with_x_links.json");
const OUTPUT_CSV = path.join(process.cwd(), "output", "rootdata_projects_with_x_links.csv");
const PROFILE_DIR = ".rootdata-chrome-profile";
const CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const START_INDEX = Number(process.env.START_INDEX || "0");
const LIMIT = Number(process.env.LIMIT || "0");
const CONCURRENCY = Number(process.env.CONCURRENCY || "5");

function csvEscape(value) {
  const text = value == null ? "" : String(value);
  if (/[",\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

function normalizeX(url) {
  if (!url) return "";
  try {
    const u = new URL(url);
    const handle = u.pathname.split("/").filter(Boolean)[0] || "";
    if (!handle || /^RootDataCrypto$/i.test(handle)) return "";
    return handle ? `https://x.com/${handle.replace(/^@/, "")}` : url;
  } catch {
    return url;
  }
}

async function extractLinks(page) {
  return page.evaluate(() => {
    const social = { x_url: "", website: "" };
    const anchors = Array.from(document.querySelectorAll("a[href]"));

    for (const a of anchors) {
      const href = a.href || "";
      const text = (a.innerText || a.textContent || "").trim();
      if (!social.x_url && /(?:twitter\.com|x\.com)\//i.test(href)) {
        social.x_url = href;
      }
      // Heuristic: project site links tend to be short and near the top, but we avoid known non-project domains.
      if (
        !social.website &&
        /^https?:\/\//.test(href) &&
        !/rootdata\.com|forms\.gle|twitter\.com|x\.com|t\.me|discord|linkedin|medium|github|apps\.apple\.com|google/i.test(href) &&
        (text === "" || text.length < 80)
      ) {
        social.website = href;
      }
    }

    return social;
  });
}

async function processRow(page, row) {
  try {
    await page.goto(row.project_url, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.waitForTimeout(900);
    const links = await extractLinks(page);
    return {
      ...row,
      website: links.website || "",
      x_url: normalizeX(links.x_url || ""),
      error: "",
    };
  } catch (error) {
    return {
      ...row,
      website: "",
      x_url: "",
      error: String(error.message || error),
    };
  }
}

async function worker(context, rows, startAt, step, onDone) {
  const page = await context.newPage();
  const out = [];
  for (let i = startAt; i < rows.length; i += step) {
    const result = await processRow(page, rows[i]);
    out.push(result);
    onDone(rows[i], i, result);
  }
  await page.close();
  return out;
}

async function main() {
  const rows = JSON.parse(fs.readFileSync(INPUT_PATH, "utf-8"));
  const slice = LIMIT > 0 ? rows.slice(START_INDEX, START_INDEX + LIMIT) : rows.slice(START_INDEX);
  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    executablePath: CHROME_PATH,
    headless: true,
    args: ["--disable-blink-features=AutomationControlled"],
  });

  let done = 0;
  const workers = [];
  const all = [];
  const onDone = (row, idx, result) => {
    done += 1;
    if (done % 25 === 0 || done === slice.length) {
      console.log(JSON.stringify({
        processed: done,
        total: slice.length,
        last: row.project_name,
        with_x: all.filter((r) => r.x_url).length + (result.x_url ? 1 : 0),
      }));
    }
  };

  for (let w = 0; w < Math.min(CONCURRENCY, slice.length); w += 1) {
    workers.push(worker(context, slice, w, Math.min(CONCURRENCY, slice.length), onDone));
  }

  const parts = await Promise.all(workers);
  for (const part of parts) all.push(...part);

  const position = new Map(slice.map((row, idx) => [row.project_url, idx]));
  all.sort((a, b) => position.get(a.project_url) - position.get(b.project_url));

  fs.writeFileSync(OUTPUT_JSON, JSON.stringify(all, null, 2), "utf-8");
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
    "website",
    "x_url",
    "error",
  ];
  const lines = [headers.join(",")];
  for (const row of all) lines.push(headers.map((k) => csvEscape(row[k])).join(","));
  fs.writeFileSync(OUTPUT_CSV, `${lines.join("\n")}\n`, "utf-8");

  console.log(JSON.stringify({ output_json: OUTPUT_JSON, output_csv: OUTPUT_CSV, count: all.length }, null, 2));
  await context.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
