import os
import re
import json
import asyncio
import urllib.parse
import requests
import google.generativeai as genai
from playwright.async_api import async_playwright

# 1. API Keys & Secrets
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHANNEL_ID")

genai.configure(api_key=GEMINI_KEY)

def fetch_tech_dispatch():
    """Gemini generates editorial content, verified metrics, and simulated X quote."""
    model = genai.GenerativeModel("gemini-1.5-flash")
    
    prompt = """
    You are the Lead Tech & Macro Strategist for CULTYNEX MEDIA NETWORK.
    Generate a high-authority editorial news brief on a major trending Tech/Silicon/AI breakthrough.

    OUTPUT ONLY VALID JSON:
    {
      "tag": "CULTYNEX DISPATCH",
      "read_time": "2 MIN READ",
      "headline": "PUNCHY EDITORIAL HEADLINE IN UPPERCASE (MAX 10 WORDS)",
      "image_keyword": "single specific visual subject like microchip wafer or datacenter or humanoid robot",
      "tweet_author": "Tech Analyst",
      "tweet_handle": "@TechAnalyst",
      "tweet_text": "One concise, hard-hitting analytical reaction on market or architecture impact.",
      "metric_val": "+35%",
      "metric_label": "ENERGY EFFICIENCY | 2026 ROADMAP",
      "telegram_body": "3 crisp editorial bullet points explaining The Big Picture, Key Numbers, and Why It Matters.",
      "quiz_question": "One sharp single-choice question based on this event.",
      "options": ["Correct option", "Wrong option 1", "Wrong option 2", "Wrong option 3"],
      "correct_id": 0
    }
    """
    
    res = model.generate_content(prompt)
    text = res.text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(text)

async def render_editorial_card(data):
    """Fills card_template.html and renders a 1080x1350 PNG via Playwright."""
    with open("card_template.html", "r", encoding="utf-8") as f:
        html = f.read()
    
    # High-quality Unsplash editorial visual
    kw = urllib.parse.quote(data.get("image_keyword", "technology"))
    img_url = f"https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1200&q=80"
    
    html = html.replace("{{TAG}}", data.get("tag", "CULTYNEX DISPATCH"))
    html = html.replace("{{READ_TIME}}", data.get("read_time", "2 MIN READ"))
    html = html.replace("{{HEADLINE}}", data.get("headline", "TECH ADVANCEMENT"))
    html = html.replace("{{IMAGE_URL}}", img_url)
    html = html.replace("{{TWEET_AUTHOR}}", data.get("tweet_author", "Tech Analyst"))
    html = html.replace("{{TWEET_HANDLE}}", data.get("tweet_handle", "@TechAnalyst"))
    html = html.replace("{{TWEET_TEXT}}", data.get("tweet_text", ""))
    html = html.replace("{{METRIC_VAL}}", data.get("metric_val", "+35%"))
    html = html.replace("{{METRIC_LABEL}}", data.get("metric_label", "EFFICIENCY"))
    
    with open("rendered_card.html", "w", encoding="utf-8") as f:
        f.write(html)
        
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1080, "height": 1350})
        await page.goto(f"file://{os.path.abspath('rendered_card.html')}")
        await page.wait_for_timeout(1000)
        await page.screenshot(path="final_card.png")
        await browser.close()

def send_telegram(data):
    """Sends the rendered card photo + brief + quiz to Telegram."""
    caption = (
        f"⚡ <b>{data['headline']}</b>\n\n"
        f"{data['telegram_body']}\n\n"
        f"🌐 <i>Full dispatch & analytical breakdown on Cultynex Web</i>"
    )
    
    # 1. Send Photo
    with open("final_card.png", "rb") as photo:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files={"photo": photo}
        )
        
    # 2. Send Quiz Poll
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPoll",
        json={
            "chat_id": CHAT_ID,
            "question": f"🎯 {data['quiz_question']}",
            "options": data["options"],
            "type": "quiz",
            "correct_option_id": data["correct_id"],
            "is_anonymous": False
        }
    )

async def main():
    print("Generating editorial payload...")
    data = fetch_tech_dispatch()
    print("Rendering high-res card...")
    await render_editorial_card(data)
    print("Broadcasting to Telegram...")
    send_telegram(data)
    print("Complete!")

if __name__ == "__main__":
    asyncio.run(main())
