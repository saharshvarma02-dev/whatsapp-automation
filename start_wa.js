const puppeteer = require('puppeteer-core');
const axios = require('axios');

console.log("==================================================");
console.log(" EXECUTING API-LEVEL CHATBOT PIPELINE");
console.log("==================================================");

const asyncioTimeout = (ms) => new Promise((res) => setTimeout(res, ms));

let isProcessing = false;
let lastProcessedMessage = "";

async function start() {
  try {
    console.log(" Fetching live dynamic address from Chrome API...");
    const chromeInfo = await axios.get('http://127.0.0.1:9222/json/version');
    const dynamicWS = chromeInfo.data.webSocketDebuggerUrl;
    
    const browser = await puppeteer.connect({
      browserWSEndpoint: dynamicWS,
      defaultViewport: null
    });

    const pages = await browser.pages();
    const waPage = pages.find(p => p.url().includes('whatsapp.com'));

    if (!waPage) {
      throw new Error("Could not find an active WhatsApp Web tab open!");
    }

    console.log(` Linked to Tab: ${await waPage.title()}`);
    console.log(' Deployed network listener layer...');

    //  LISTENER: Forwards clean strings to FastAPI and types out responses inline
    await waPage.exposeFunction('forwardToBot', async (messageData) => {
      if (isProcessing || messageData.body === lastProcessedMessage) {
        return;
      }
      
      try {
        isProcessing = true;
        lastProcessedMessage = messageData.body;
        
        console.log(`\n Message Detected: "${messageData.body}"`);
        console.log(`    Sending to FastAPI backend...`);
        
        const response = await axios.post('http://127.0.0.1:8000/webhook', messageData);
        
        if (response.data && response.data.reply) {
          const aiReply = response.data.reply;
          lastProcessedMessage = aiReply;
          
          console.log(`    AI response: "${aiReply}"`);
          console.log(`    Typing reply back into WhatsApp...`);
          
          // Target the fallback container wrapper cleanly
          const inputElement = await waPage.waitForSelector('div[contenteditable="true"]', { timeout: 3000 });

          if (!inputElement) {
            throw new Error("Could not target the text field on screen.");
          }
          
          await inputElement.focus();
          await waPage.evaluate((el) => {
            el.focus();
            document.execCommand('selectAll', false, null);
          }, inputElement);
          
          await waPage.keyboard.sendCharacter(aiReply);
          await asyncioTimeout(600); 
          await waPage.keyboard.press('Enter');
          console.log('    Reply sent successfully!');
          
          lastProcessedMessage = aiReply;
        }
      } catch (err) {
        console.error('    Python Backend Error:', err.message);
      } finally {
        await asyncioTimeout(2500);
        isProcessing = false;
      }
    });

    // 🚀 STABILIZED NETWORK AGENT: Strict packet sniffing to eliminate ghost triggers entirely
    await waPage.evaluate(() => {
      if (window.WWebInterceptionActive) return;
      window.WWebInterceptionActive = true;

      const originalPush = Array.prototype.push;
      Array.prototype.push = function (...args) {
        for (const item of args) {
          if (
            item && 
            typeof item === 'object' && 
            item.body && 
            item.id && 
            !item.id.fromMe &&            // Must not be sent by the bot
            item.type === 'chat' &&        // Must be a standard text chat
            !item.isStatusV3 &&            // Ignore status/story updates
            item.isNewMsg === true         // 🚀 CRITICAL: Must be an actual newly arrived push notification packet
          ) {
            // Drop system text or encryption filler flags cleanly
            if (!item.body.includes('end-to-end encrypted') && item.body.trim().length > 0) {
              window.forwardToBot({
                body: item.body.trim(),
                from: item.from || 'Incoming Chat'
              });
            }
          }
        }
        return originalPush.apply(this, args);
      };
      console.log(" High-security network interception channel deployed.");
    });

  } catch (err) {
    console.error('\n PIPELINE FAILURE:', err.message);
  }
}

start();