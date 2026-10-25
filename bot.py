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

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHANNEL_ID")

genai.configure(api_key=GEMINI_KEY)

TECH_DOMAINS = [
    "Silicon Photonics & Optical Interconnect Acceleration",
    "Sub-2nm Gate-All-Around (GAA) & Backside Power Architecture",
    "High-NA EUV Lithography & Angstrom-Era Semiconductor Foundries",
    "Neuromorphic Silicon & Analog In-Memory Edge AI",
    "Quantum Advantage with Superconducting Qubits & Topological Braiding",
    "Exascale Liquid Immersion Supercomputing & Geothermal Data Infrastructure",
    "Humanoid Spatial Intelligence & High-Torque Brushless Actuators",
    "Orbital Laser Cross-Links & Low-Earth Photonic Mesh Networks"
]

# Curated, guaranteed-active 4K/UHD Deep Tech images
PREMIUM_TECH_IMAGES = [
    "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1629654297299-c8506221ca97?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1517420704952-d9f39e95b43e?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1504384308090-c894fdcc538d?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1581092160607-ee22621dd758?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=1400&q=85"
]

def fetch_tech_dispatch():
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

    model = genai.GenerativeModel(working_model)
    selected_domain = random.choice(TECH_DOMAINS)
    random_seed = random.randint(10000, 99999)
    
    prompt = f"""
    You are the Chief Intelligence Officer at CULTYNEX MEDIA NETWORK.
    Produce an elite, analytical dispatch on: {selected_domain}.
    Seed: {random_seed}. Tone: Bloomberg Intelligence meets Wired.

    OUTPUT ONLY VALID JSON:
    {{
      "tag": "CULTYNEX DISPATCH",
      "read_time": "2 MIN READ",
      "headline": "SHORT HARD-HITTING UPPERCASE HEADLINE (MAX 8 WORDS)",
      "tweet_author": "Marcus Vance",
      "tweet_handle": "@CultynexStrat",
      "tweet_text": "One razor-sharp technical judgment on how this architecture changes market dynamics.",
      "metric_val": "+48%",
      "metric_label": "THROUGHPUT MULTIPLIER | 2026 ROADMAP",
      "telegram_big_picture": "One crisp sentence explaining the structural breakthrough.",
      "telegram_numbers": "One sentence detailing the exact performance, power, or latency metrics.",
      "telegram_strategic": "One sentence explaining who wins, who loses, and the market consequence.",
      "quiz_question": "One technical question testing core understanding of this architecture.",
      "options": ["Accurate Technical Answer", "Plausible Distractor 1", "Plausible Distractor 2", "Plausible Distractor 3"],
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
    with open("card_template.html", "r", encoding="utf-8") as f:
        template = f.read()
    
    # Pick randomly from the verified tech visual gallery
    img_url = random.choice(PREMIUM_TECH_IMAGES)
    
    html_content = template
    html_content = html_content.replace("{{TAG}}", data.get("tag", "CULTYNEX DISPATCH"))
    html_content = html_content.replace("{{READ_TIME}}", data.get("read_time", "2 MIN READ"))
    html_content = html_content.replace("{{HEADLINE}}", data.get("headline", "SILICON ADVANCEMENT"))
    html_content = html_content.replace("{{IMAGE_URL}}", img_url)
    html_content = html_content.replace("{{TWEET_AUTHOR}}", data.get("tweet_author", "Marcus Vance"))
    html_content = html_content.replace("{{TWEET_HANDLE}}", data.get("tweet_handle", "@CultynexStrat"))
    html_content = html_content.replace("{{TWEET_TEXT}}", data.get("tweet_text", ""))
    html_content = html_content.replace("{{METRIC_VAL}}", data.get("metric_val", "+40%"))
    html_content = html_content.replace("{{METRIC_LABEL}}", data.get("metric_label", "PERFORMANCE VECTOR"))
    
    with open("rendered_card.html", "w", encoding="utf-8") as f:
        f.write(html_content)
        
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1080, "height": 1350})
        await page.goto(f"file://{os.path.abspath('rendered_card.html')}")
        await page.wait_for_timeout(2500)
        await page.screenshot(path="final_card.png")
        await browser.close()

def send_telegram(data):
    headline = html.escape(data.get("headline", "CULTYNEX INTEL"))
    big_pic = html.escape(data.get("telegram_big_picture", ""))
    numbers = html.escape(data.get("telegram_numbers", ""))
    strategic = html.escape(data.get("telegram_strategic", ""))

    caption = (
        f"⚡ <b>{headline}</b>\n\n"
        f"▫️ <b>THE SHIFT:</b> {big_pic}\n\n"
        f"▫️ <b>THE NUMBERS:</b> {numbers}\n\n"
        f"▫️ <b>STRATEGIC IMPACT:</b> {strategic}\n\n"
        f"🌐 <i>Cultynex Terminal • Intelligence Feed</i>"
    )
    
    with open("final_card.png", "rb") as photo:
        r_photo = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files={"photo": photo}
        )
        print("Telegram SendPhoto:", r_photo.status_code)
        r_photo.raise_for_status()

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
    print("Telegram SendPoll:", r_poll.status_code)
    r_poll.raise_for_status()

async def main():
    print("Generating editorial payload...")
    data = fetch_tech_dispatch()
    print("Rendering high-res card...")
    await render_editorial_card(data)
    print("Publishing to Telegram...")
    send_telegram(data)
    print("Broadcast Complete!")

if __name__ == "__main__":
    asyncio.run(main())
