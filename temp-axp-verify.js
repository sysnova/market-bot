const { chromium } = require("playwright");
const { pathToFileURL } = require("url");
(async()=>{
  const browser = await chromium.launch({headless:true, executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const page = await browser.newPage({viewport:{width:736,height:900}, colorScheme:'light'});
  const errs=[];
  page.on('pageerror', e=>errs.push('pageerror: '+e.message));
  page.on('console', m=>{ if(m.type()==='error') errs.push('console: '+m.text()); });
  await page.goto(pathToFileURL('C:/Users/lgonz/.codex/visualizations/2026/08/15/01a00561-3cbc-7003-a305-523bc3780506/axp-three-engine-day-path-preview.html').href, {waitUntil:'networkidle'});
  await page.waitForTimeout(5000);
  const info = await page.evaluate(() => ({svgs: document.querySelectorAll('svg').length, text: document.body.innerText.slice(0,300)}));
  console.log(JSON.stringify({errs, info}, null, 2));
  await browser.close();
})();
