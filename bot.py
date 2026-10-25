import os
import re
import json
import random
import asyncio
import urllib.parse
import html
import requests
from datetime import datetime
import google.generativeai as genai
from playwright.async_api import async_playwright

# 1. API Keys & Secrets
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHANNEL_ID")

genai.configure(api_key=GEMINI_KEY)

# High-Quota priority fallback list to avoid 429 quota exhaustion
FALLBACK_MODELS = [
    "models/gemini-2.5-flash",
    "models/gemini-1.5-flash",
    "models/gemini-3.8-flash",
    "models/gemini-2.5-pro",
    "models/gemini-1.5-pro"
]

# 2. Tech Intelligence Tracks
TECH_DOMAINS = [
    "Silicon Photonics & Co-Packaged Optics in AI Clusters",
    "Analog In-Memory Computing & Neuromorphic Crossbar Arrays",
    "Sub-1.4nm Angstrom Lithography & High-NA EUV Scanner Limits",
    "Superconducting Transmon Qubits & Quantum Error Correction",
    "Direct Immersion Liquid Cooling in Multi-Megawatt AI Data Centers",
    "High-Density 3D Hybrid Wafer Stacking & Die-to-Die Interconnects"
]

# 3. Curated High-Res Dark Hardware Visuals
CURATED_SILICON_IMAGES = [
    "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1629654297299-c8506221ca97?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1597733336794-12d05021d510?auto=format&fit=crop&w=1400&q=85",
    "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=1400&q=85"
]

# 4. Rotating Lead Magnets / Community CTAs
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

def generate_with_fallback(prompt):
    """Executes prompt against high-quota Gemini models sequentially to bypass 429 quota exhaustion."""
    for model_name in FALLBACK_MODELS:
        try:
            print(f"Attempting generation with: {model_name}")
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            print(f"Model {model_name} quota/error: {e}. Switching to next candidate...")
            continue
    raise RuntimeError("All configured Gemini models failed or hit quota limits.")

def fetch_tech_dispatch():
    """Generates structured payload for Telegram post, quiz, and metadata."""
    domain = random.choice(TECH_DOMAINS)
    seed = random.randint(10000, 99999)
    
    prompt = f"""
    You are the Senior Intelligence Editor for CULTYNEX MEDIA NETWORK.
    Focus on this domain: {domain}. Seed: {seed}.
    Create a punchy viral-intel dispatch optimized to drive traffic to our terminal website.

    OUTPUT ONLY VALID JSON:
    {{
      "tag": "CULTYNEX DISPATCH",
      "read_time": "3 MIN READ",
      "headline": "SHORT PUNCHY UPPERCASE HEADLINE (MAX 8 WORDS)",
      "tweet_author": "Marcus Vance",
      "tweet_handle": "@CultynexStrat",
      "tweet_text": "One razor-sharp decisive judgment on industry disruption.",
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
    
    raw_text = generate_with_fallback(prompt)
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(raw_text)

def generate_and_publish_web_story(data, img_url):
    """Writes an exhaustive, SEO-optimized 800+ word article and saves to articles/ folder."""
    os.makedirs("articles", exist_ok=True)
    
    clean_slug = re.sub(r'[^a-zA-Z0-9]+', '-', data.get("headline", "intel-brief").lower()).strip('-')
    file_path = f"articles/{clean_slug}.html"
    
    article_prompt = f"""
    You are an elite Investigative Technology Journalist at The Hindu / Wired level.
    Write an exhaustive, authoritative 800-to-1000 word deep-dive article based on:
    Headline: {data.get('headline')}
    Core Premise: {data.get('curiosity_teaser')}
    Key Metric: {data.get('metric_val')} ({data.get('metric_label')})

    FORMAT STRICTLY AS CLEAN HTML BODY TAGS (Use <h2>, <p>, <blockquote>, <strong>).
    DO NOT wrap in ```html codeblocks. DO NOT include <html>, <head>, or <body> tags.

    Must include these 3 comprehensive sections with <h2> headers:
    1. <h2>The Structural Bottleneck & Background Context</h2>
    2. <h2>Engineering Architecture & Performance Teardown</h2>
    3. <h2>Strategic Industry Fallout: Winners, Losers, and the 2026-2028 Horizon</h2>

    Make it rich with technical specifics, silicon physical laws, and industry analysis.
    """
    
    article_raw = generate_with_fallback(article_prompt)
    article_body = article_raw.replace("```html", "").replace("```", "").strip()

    template_content = ""
    if os.path.exists("article_template.html"):
        with open("article_template.html", "r", encoding="utf-8") as f:
            template_content = f.read()
    else:
        template_content = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <meta name="viewport" content="width=device-width, initial-scale=1.0">
          <title>{{HEADLINE}} | Cultynex Intelligence</title>
          <style>
            body { background: #07080b; color: #d6d9e0; font-family: sans-serif; padding: 20px; line-height: 1.6; max-width: 900px; margin: auto; }
            h1 { color: #fff; font-size: 34px; margin-bottom: 20px; }
            h2 { color: #fff; margin-top: 30px; }
            img { width: 100%; max-height: 450px; object-fit: cover; border-radius: 10px; margin-bottom: 25px; }
            .box { background: #11141d; padding: 18px; border-left: 4px solid #00ff88; margin-bottom: 25px; border-radius: 6px; }
          </style>
        </head>
        <body>
          <h1>{{HEADLINE}}</h1>
          <img src="{{IMAGE_URL}}" alt="{{HEADLINE}}">
          <div class="box"><strong>Key Highlights:</strong><br>{{HIGHLIGHTS_HTML}}</div>
          <div>{{ARTICLE_CONTENT_HTML}}</div>
        </body>
        </html>
        """

    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    now_pretty = datetime.utcnow().strftime("%b %d, %Y")
    
    highlights_html = f"""
    <li><strong>• Breakthrough Core:</strong> {html.escape(data.get('telegram_hook', 'Key architectural advancement.'))}</li>
    <li><strong>• Measured Impact:</strong> {html.escape(data.get('metric_val', ''))} performance jump across {html.escape(data.get('metric_label', 'operational matrices'))}.</li>
    <li><strong>• Strategic Horizon:</strong> {html.escape(data.get('curiosity_teaser', ''))}</li>
    """

    full_page = template_content
    full_page = full_page.replace("{{HEADLINE}}", data.get("headline", "TECH INTEL BRIEF"))
    full_page = full_page.replace("{{EXCERPT}}", data.get("curiosity_teaser", "")[:150])
    full_page = full_page.replace("{{IMAGE_URL}}", img_url)
    full_page = full_page.replace("{{CATEGORY}}", data.get("tag", "CULTYNEX DISPATCH"))
    full_page = full_page.replace("{{READ_TIME}}", "4")
    full_page = full_page.replace("{{DATE_PRETTY}}", now_pretty)
    full_page = full_page.replace("{{DATE_ISO}}", now_iso)
    full_page = full_page.replace("{{HIGHLIGHTS_HTML}}", highlights_html)
    full_page = full_page.replace("{{ARTICLE_CONTENT_HTML}}", article_body)

    with open(file_path, "w", encoding="utf-8") as out:
        out.write(full_page)
        
    print(f"Generated Web Article: {file_path}")
    return f"[https://realmrajaysingh-dev.github.io/telegram-commuter-bot/articles/](https://realmrajaysingh-dev.github.io/telegram-commuter-bot/articles/){clean_slug}.html"

async def render_editorial_card(data, img_url):
    """Renders 1080x1350 card for Telegram."""
    with open("card_template.html", "r", encoding="utf-8") as f:
        template = f.read()
    
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

def send_telegram(data, direct_article_url):
    """Sends visual photo, marketing hook, direct website link, and quiz to Telegram."""
    headline = html.escape(data.get("headline", "CULTYNEX INTEL"))
    hook = html.escape(data.get("telegram_hook", ""))
    teaser = html.escape(data.get("curiosity_teaser", ""))
    
    offer = random.choice(LEAD_MAGNETS)

    caption = (
        f"⚡ <b>{headline}</b>\n\n"
        f"{hook}\n\n"
        f"🔍 <b>THE BREAKDOWN:</b> {teaser}\n\n"
        f"👉 <i>Full investigative report, specs & architecture breakdowns available online:</i>\n"
        f"🔗 <a href='{direct_article_url}'><b>Read Detailed Article On Cultynex Web ↗</b></a>\n\n"
        f"──────────────\n"
        f"<b>{offer['title']}</b>\n"
        f"💡 <i>{offer['desc']}</i>\n"
        f"──────────────"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "📖 Read Full Story On Website", "url": direct_article_url}
            ],
            [
                {"text": offer["btn_text"], "url": offer["btn_url"]}
            ]
        ]
    }
    
    with open("final_card.png", "rb") as photo:
        r_photo = requests.post(
            f"[https://api.telegram.org/bot](https://api.telegram.org/bot){BOT_TOKEN}/sendPhoto",
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

    r_poll = requests.post(
        f"[https://api.telegram.org/bot](https://api.telegram.org/bot){BOT_TOKEN}/sendPoll",
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
    print("1. Creating promotional intelligence dispatch...")
    data = fetch_tech_dispatch()
    
    selected_image = random.choice(CURATED_SILICON_IMAGES)

    print("2. Generating comprehensive 800+ word website article...")
    article_url = generate_and_publish_web_story(data, selected_image)

    print("3. Rendering high-res editorial card...")
    await render_editorial_card(data, selected_image)

    print("4. Publishing payload to Telegram channel...")
    send_telegram(data, article_url)
    
    print("Dispatch deployed successfully!")

if __name__ == "__main__":
    asyncio.run(main())
