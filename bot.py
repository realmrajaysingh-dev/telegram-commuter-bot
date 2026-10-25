import os
import re
import json
import random
import asyncio
import urllib.parse
import html
import requests
import google.generativeai as genai
from playwright.async_api import async_playwright

# 1. API Keys & Secrets
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHANNEL_ID")

genai.configure(api_key=GEMINI_KEY)

# Dynamic Tech Vectors for variety
TECH_DOMAINS = [
    "Neuromorphic Computing & Brain-Inspired Silicon",
    "Quantum Advantage & Topological Qubits",
    "Next-Gen Humanoid Robotics Actuators & Spatial Intelligence",
    "Hyperscale Nuclear/Geothermal Powered AI Data Centers",
    "Direct Liquid Immersion Cooling for Exascale Supercomputers",
    "Open-Source Frontier LLM Architecture & Post-Transformer Attention",
    "Space-Based Optical Interconnects & Starlink Laser Mesh",
    "DNA Data Storage & Bio-Silicon Hybrid Chips"
]

def fetch_tech_dispatch():
    """Generates editorial brief and card metrics via available Gemini model."""
    working_model = "gemini-3.8-flash"
    try:
        available = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
        for candidate in ["models/gemini-3.8-flash", "models/gemini-2.5-flash", "models/gemini-1.5-flash", "models/gemini-pro"]:
            if candidate in available:
                working_model = candidate
                break
        else:
            if available:
                working_model = available[0]
    except Exception:
        working_model = "gemini-3.8-flash"

    print(f"Using Gemini Model: {working_model}")
    model = genai.GenerativeModel(working_model)
    
    selected_domain = random.choice(TECH_DOMAINS)
    random_seed = random.randint(1000, 9999)
    
    prompt = f"""
    You are the Lead Tech & Macro Strategist for CULTYNEX MEDIA NETWORK.
    Focus strictly on this domain: {selected_domain}.
    Generate an entirely fresh, highly-authoritative editorial intelligence brief on a groundbreaking innovation.
    Seed: {random_seed} (ensure uniqueness, never repeat recent topics).

    OUTPUT ONLY VALID JSON:
    {{
      "tag": "CULTYNEX DISPATCH",
      "read_time": "2 MIN READ",
      "headline": "PUNCHY EDITORIAL HEADLINE IN UPPERCASE (MAX 10 WORDS)",
      "image_keyword": "2-3 precise words for unsplash photo (e.g. quantum server, humanoid robot, laser optics, server farm)",
      "tweet_author": "Tech Analyst",
      "tweet_handle": "@TechAnalyst",
      "tweet_text": "One concise, hard-hitting analytical reaction on market or architecture impact.",
      "metric_val": "+42%",
      "metric_label": "LATENCY REDUCTION | PRODUCTION ROADMAP",
      "telegram_body": "3 crisp editorial bullet points explaining The Big Picture, Key Numbers, and Why It Matters.",
      "quiz_question": "One sharp single-choice question based on this event.",
      "options": ["Correct option", "Wrong option 1", "Wrong option 2", "Wrong option 3"],
      "correct_id": 0
    }}
    """
    
    res = model.generate_content(prompt)
    text = res.text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(text)

async def render_editorial_card(data):
    """Fills card_template.html and renders a 1080x1350 PNG via Playwright with dynamic image."""
    with open("card_template.html", "r", encoding="utf-8") as f:
        template = f.read()
    
    # Keyword-based dynamic unsplash photo with cache buster
    raw_kw = data.get("image_keyword", "technology server")
    clean_kw = urllib.parse.quote(raw_kw)
    rand_id = random.randint(1, 10000)
    img_url = f"https://source.unsplash.com/1200x800/?{clean_kw}&sig={rand_id}"
    
    html_content = template
    html_content = html_content.replace("{{TAG}}", data.get("tag", "CULTYNEX DISPATCH"))
    html_content = html_content.replace("{{READ_TIME}}", data.get("read_time", "2 MIN READ"))
    html_content = html_content.replace("{{HEADLINE}}", data.get("headline", "TECH ADVANCEMENT"))
    html_content = html_content.replace("{{IMAGE_URL}}", img_url)
    html_content = html_content.replace("{{TWEET_AUTHOR}}", data.get("tweet_author", "Tech Analyst"))
    html_content = html_content.replace("{{TWEET_HANDLE}}", data.get("tweet_handle", "@TechAnalyst"))
    html_content = html_content.replace("{{TWEET_TEXT}}", data.get("tweet_text", ""))
    html_content = html_content.replace("{{METRIC_VAL}}", data.get("metric_val", "+35%"))
    html_content = html_content.replace("{{METRIC_LABEL}}", data.get("metric_label", "EFFICIENCY"))
    
    with open("rendered_card.html", "w", encoding="utf-8") as f:
        f.write(html_content)
        
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1080, "height": 1350})
        await page.goto(f"file://{os.path.abspath('rendered_card.html')}")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="final_card.png")
        await browser.close()

def send_telegram(data):
    """Sends the rendered card photo + brief + quiz to Telegram."""
    clean_headline = html.escape(data.get("headline", "CULTYNEX DISPATCH"))
    clean_body = html.escape(data.get("telegram_body", ""))
    
    caption = (
        f"⚡ <b>{clean_headline}</b>\n\n"
        f"{clean_body}\n\n"
        f"🌐 <i>Full dispatch & analytical breakdown on Cultynex Web</i>"
    )
    
    # 1. Send Card Photo
    with open("final_card.png", "rb") as photo:
        r_photo = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files={"photo": photo}
        )
        print("Telegram SendPhoto:", r_photo.status_code, r_photo.text)
        r_photo.raise_for_status()

    # 2. Send Quiz Poll
    r_poll = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPoll",
        json={
            "chat_id": CHAT_ID,
            "question": f"🎯 {data['quiz_question'][:255]}",
            "options": data["options"][:4],
            "type": "quiz",
            "correct_option_id": int(data.get("correct_id", 0)),
            "is_anonymous": True
        }
    )
    print("Telegram SendPoll:", r_poll.status_code, r_poll.text)
    r_poll.raise_for_status()

async def main():
    print("Fetching editorial dispatch...")
    data = fetch_tech_dispatch()
    print("Rendering editorial card...")
    await render_editorial_card(data)
    print("Publishing to Telegram...")
    send_telegram(data)
    print("Successfully Published!")

if __name__ == "__main__":
    asyncio.run(main())
