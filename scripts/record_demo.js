// Scripted demo recording of the review UI.
// Usage: node scripts/record_demo.js <clip|stills> <outdir> [base-url]
// Needs puppeteer-core and a local Chrome; the server must be running.
const puppeteer = require("puppeteer-core");
const path = require("path");

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const [mode, outdir, BASE = "http://localhost:8010"] = process.argv.slice(2);
const W = 1440, H = 900;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Headless recordings show no cursor, so draw one with a click ripple.
const CURSOR = `
(() => {
  const add = () => {
    if (document.getElementById('demo-cursor')) return;
    const c = document.createElement('div');
    c.id = 'demo-cursor';
    c.style.cssText = 'position:fixed;left:-50px;top:-50px;width:18px;height:18px;border-radius:50%;background:rgba(255,255,255,0.9);border:2px solid #0b0f14;box-shadow:0 0 0 3px rgba(126,226,168,0.55);z-index:99999;pointer-events:none;transform:translate(-50%,-50%);transition:transform 0.08s';
    document.body.appendChild(c);
    document.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    document.addEventListener('mousedown', e => {
      c.style.transform = 'translate(-50%,-50%) scale(0.7)';
      const r = document.createElement('div');
      r.style.cssText = 'position:fixed;left:' + e.clientX + 'px;top:' + e.clientY + 'px;width:12px;height:12px;border-radius:50%;border:2px solid #7ee2a8;transform:translate(-50%,-50%);z-index:99998;pointer-events:none;transition:all 0.5s ease-out;opacity:1';
      document.body.appendChild(r);
      requestAnimationFrame(() => { r.style.width = '56px'; r.style.height = '56px'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 600);
    }, true);
    document.addEventListener('mouseup', () => { c.style.transform = 'translate(-50%,-50%)'; }, true);
  };
  if (document.body) add(); else document.addEventListener('DOMContentLoaded', add);
})();`;

async function click(page, selector, pause = 450) {
  const el = await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await el.evaluate((n) => n.scrollIntoView({ block: "center", behavior: "smooth" }));
  await sleep(500);
  const b = await el.boundingBox();
  await page.mouse.move(b.x + Math.min(b.width / 2, 90), b.y + b.height / 2, { steps: 22 });
  await sleep(pause);
  await page.mouse.down();
  await sleep(90);
  await page.mouse.up();
}

async function pageLoaded(page) {
  await page.waitForFunction(() => {
    const img = document.querySelector("#viewer img");
    return img && img.complete && img.naturalWidth > 0;
  }, { timeout: 60000 });
  await sleep(700);
}

async function clip(page) {
  await page.goto(`${BASE}/?q=scope2`, { waitUntil: "networkidle0" });
  await pageLoaded(page);
  await page.mouse.move(700, 300, { steps: 1 });
  await sleep(1800);
  await click(page, ".cite:nth-of-type(2)");
  await pageLoaded(page);
  await sleep(1500);
  // Scroll the middle column to the arithmetic checks.
  await page.evaluate(() => [...document.querySelectorAll("#mid .card")].find(c => c.textContent.includes("Checked by code"))?.scrollIntoView({ behavior: "smooth", block: "start" }));
  await sleep(2400);
  await click(page, "[data-c='0']");
  await pageLoaded(page);
  await sleep(2600);
  await sleep(1500);
  await click(page, "[data-q='by_gas']");
  await pageLoaded(page);
  await sleep(3200);
  await click(page, "[data-q='targets']");
  await pageLoaded(page);
  await sleep(1500);
  // End on the one answer that is incomplete, with its hand check.
  await page.evaluate(() => [...document.querySelectorAll("#mid .card")].find(c => c.textContent.includes("read by hand"))?.scrollIntoView({ behavior: "smooth", block: "center" }));
  await sleep(3500);
}

async function stills(page) {
  const shots = [
    ["01-answer", "?q=scope1", null],
    ["02-conflict", "?q=scope1", "[data-c='0']"],
    ["03-missing-evidence", "?q=by_gas", null],
  ];
  for (const [name, q, sel] of shots) {
    await page.goto(`${BASE}/${q}`, { waitUntil: "networkidle0" });
    if (sel) await page.click(sel);
    if (name !== "03-missing-evidence") await pageLoaded(page);
    await sleep(900);
    await page.screenshot({ path: path.join(outdir, `${name}.png`) });
  }
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: [`--window-size=${W},${H}`, "--hide-scrollbars"],
    defaultViewport: { width: W, height: H, deviceScaleFactor: 1 },
  });
  const page = await browser.newPage();
  await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "dark" }]);
  try {
    if (mode === "stills") {
      await stills(page);
    } else {
      await page.evaluateOnNewDocument(CURSOR);
      const rec = await page.screencast({ path: path.join(outdir, "demo.webm") });
      await clip(page);
      await sleep(500);
      // stop() can hang waiting on ffmpeg; the file is complete by then.
      await Promise.race([rec.stop(), sleep(8000)]);
    }
  } finally {
    await Promise.race([browser.close(), sleep(5000)]);
  }
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
