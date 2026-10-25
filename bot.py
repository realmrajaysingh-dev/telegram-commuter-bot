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

# Tech intelligence tracks
TECH_DOMAINS = [
    "Silicon Photonics & Co-Packaged Optics in AI Clusters",
    "Analog In-Memory Computing & Neuromorphic Crossbar Arrays",
    "Sub-1.4nm Angstrom Lithography & High-NA EUV Scanner Limits",
    "Superconducting Transmon Qubits & Quantum Error Correction",
    "Direct Immersion Liquid Cooling in Multi-Megawatt AI Data Centers",
    "High-Density 3D Hybrid Wafer Stacking & Die-to-Die Interconnects"
]

# Verified dark silicon visuals
CURATED_SILICON_IMAGES = [
    "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1629654297299-c8506221ca97?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1597733336794-12d05021d510?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=1400&q=85"
]

# Rotating High-Value Free Community Resources / Lead Magnets
LEAD_MAGNETS = [
    {
        "title": "🎁 FREE RESOURCE: 2026 AI Silicon & Hardware Master Roadmap (PDF)",
        "desc": "Foundry economics, chiplet architectures, and GPU cluster benchmarks.",
        "btn_text": "📥 Download Free Roadmap",
        "btn_url": "https://realmrajaysingh-dev.github.io/telegram-commuter-bot/"
    },
    {
        "title": "🚀 EXCLUSIVE: Master Prompt Pipeline & AI Workflow Kit",
        "desc": "Deploy 100% autonomous digital agents without writing code.",
        "btn_text": "⚡ Grab Master Pack",
        "btn_url": "https://realmrajaysingh-dev.github.io/telegram-commuter-bot/"
    },
    {
        "title": "💎 CULTYNEX ALPHA: Private Builder Community & Deal Flow",
        "desc": "Join 1,000+ engineers, founders, and macro strategists.",
        "btn_text": "🔥 Join Alpha Hub",
        "btn_url": "https://t.me/cultynex_daily"
    }
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
    domain = random.choice(TECH_DOMAINS)
    seed = random.randint(10000, 99999)
    
    prompt = f"""
    You are the Senior Editor for CULTYNEX MEDIA NETWORK.
    Focus: {domain}. Seed: {seed}.
    Create a punchy viral-intel dispatch optimized to drive traffic to our terminal website.

    OUTPUT ONLY VALID JSON:
    {{
      "tag": "CULTYNEX DISPATCH",
      "read_time": "2 MIN READ",
      "headline": "SHORT PUNCHY UPPERCASE HEADLINE (MAX 8 WORDS)",
      "tweet_author": "Marcus Vance",
      "tweet_handle": "@CultynexStrat",
      "tweet_text": "One razor-sharp judgment on industry disruption.",
      "metric_val": "+45%",
      "metric_label": "ENERGY SAVINGS | 2026 BENCHMARK",
      "article_slug": "silicon-architecture-breakthrough",
      "telegram_hook": "2 ultra-crisp bullet points detailing the core technical breakthrough and performance metrics.",
      "curiosity_teaser": "One sentence explaining why legacy tech is failing and how the full analysis is on the terminal.",
      "quiz_question": "One deep technical question evaluating understanding.",
      "options": ["Correct Technical Answer", "Alternative 1", "Alternative 2", "Alternative 3"],
      "correct_id": 0,
      "quiz_explanation": "One clear sentence explaining the technical reason."
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
    
    img_url = random.choice(CURATED_SILICON_IMAGES)
    
    html_content = template
    html_content = html_content.replace("{{TAG}}", data.get("tag", "CULTYNEX DISPATCH"))
    html_content = html_content.replace("{{READ_TIME}}", data.get("read_time", "2 MIN READ"))
    html_content = html_content.replace("{{HEADLINE}}", data.get("headline", "SILICON ADVANCEMENT"))
    html_content = html_content.replace("{{IMAGE_URL}}", img_url)
    html_content = html_content.replace("{{TWEET_AUTHOR}}", data.get("tweet_author", "Marcus Vance"))
    html_content = html_content.replace("{{TWEET_HANDLE}}", data.get("tweet_handle", "@CultynexStrat"))
    html_content = html_content.replace("{{TWEET_TEXT}}", data.get("tweet_text", ""))
    html_content = html_content.replace("{{METRIC_VAL}}", data.get("metric_val", "+45%"))
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
    headline = html.escape(data.get("headline", "CULTYNEX INTEL"))
    hook = html.escape(data.get("telegram_hook", ""))
    teaser = html.escape(data.get("curiosity_teaser", ""))
    slug = data.get("article_slug", "intel-brief")
    
    # Lead Magnet Offer
    offer = random.choice(LEAD_MAGNETS)
    
    terminal_url = f"https://realmrajaysingh-dev.github.io/telegram-commuter-bot/?article={slug}"

    caption = (
        f"⚡ <b>{headline}</b>\n\n"
        f"{hook}\n\n"
        f"🔍 <b>THE BREAKDOWN:</b> {teaser}\n\n"
        f"👉 <i>Full unredacted research report & raw metrics available on Terminal:</i>\n"
        f"🔗 <a href='{terminal_url}'><b>Read Full Analysis On Cultynex Web ↗</b></a>\n\n"
        f"──────────────\n"
        f"<b>{offer['title']}</b>\n"
        f"💡 <i>{offer['desc']}</i>\n"
        f"──────────────"
    )

    # 2 Big Action Buttons below post
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "📖 Read Full Story On Website", "url": terminal_url}
            ],
            [
                {"text": offer["btn_text"], "url": offer["btn_url"]}
            ]
        ]
    }
    
    # 1. Post Visual Card + Hook + Promo + Buttons
    with open("final_card.png", "rb") as photo:
        r_photo = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={
                "chat_id": CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": json.dumps(reply_markup)
            },
            files={"photo": photo}
        )
        print("Telegram SendPhoto:", r_photo.status_code)
        r_photo.raise_for_status()

    # 2. Quiz Poll with explanation
    r_poll = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPoll",
        json={
            "chat_id": CHAT_ID,
            "question": f"🎯 {data['quiz_question'][:255]}",
            "options": data["options"][:4],
            "type": "quiz",
            "correct_option_id": int(data.get("correct_id", 0)),
            "explanation": data.get("quiz_explanation", "Check full story on Cultynex Web.")[:200],
            "is_anonymous": True
        }
    )
    print("Telegram SendPoll:", r_poll.status_code)
    r_poll.raise_for_status()

async def main():
    print("Creating promotional intelligence dispatch...")
    data = fetch_tech_dispatch()
    print("Rendering media card...")
    await render_editorial_card(data)
    print("Publishing to Telegram channel...")
    send_telegram(data)
    print("Dispatch deployed successfully!")

if __name__ == "__main__":
    asyncio.run(main())
