const { chromium } = require('C:/Users/lgonz/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  try {
    const page=await browser.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    for(const [width,theme] of [[736,'light'],[360,'light'],[1024,'dark']]){
      await page.setViewportSize({width,height:1000});
      await page.emulateMedia({colorScheme:theme});
      await page.goto('file:///C:/Users/lgonz/Projects/market-bot/output/uuuu-engine-nuevo-preview.html');
      const frame=page.frameLocator('iframe');
      await frame.locator('.candle').first().waitFor();
      const state=await frame.locator('#uuuu-new').evaluate(el=>({
        candles:el.querySelectorAll('.candle').length,
        overflow:el.scrollWidth>el.clientWidth,
        title:el.querySelector('h3').textContent,
        series:Array.from(el.querySelectorAll('.candle rect:not([data-chart-hit])')).slice(0,2).map(n=>getComputedStyle(n).fill)
      }));
      await frame.locator('.candle').nth(4).hover();
      await page.screenshot({path:`C:/Users/lgonz/Projects/market-bot/output/uuuu-new-${width}.png`,fullPage:true});
      console.log(JSON.stringify({width,theme,...state,errors}));
    }
  } finally {await browser.close();}
})();
