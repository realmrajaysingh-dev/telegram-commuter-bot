import os
import json
import time
import requests
from io import BytesIO
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from google import genai

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_CHAT_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

# Ubuntu GitHub Actions par built-in fonts load karne ke liye
def get_font(size):
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
    ]
    for p in font_paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

def get_regular_font(size):
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
    ]
    for p in font_paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

# --- 1. RICH HIGH-IMPACT 1080x1080 EDITORIAL CARD ---
def generate_cinematic_card(category_title, headline, bullet_one, bullet_two):
    width, height = 1080, 1080
    image = Image.new("RGBA", (width, height), (12, 16, 26, 255))
    draw = ImageDraw.Draw(image)

    # Gradient Canvas Background
    for y in range(height):
        ratio = y / height
        r = int(10 + (28 - 10) * ratio)
        g = int(14 + (20 - 14) * ratio)
        b = int(24 + (52 - 24) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

    # Fonts Load
    font_badge = get_font(28)
    font_brand = get_font(24)
    font_tag = get_font(22)
    font_headline = get_font(42)
    font_bullet_title = get_font(28)
    font_bullet_body = get_regular_font(26)
    font_footer = get_font(22)

    # Exterior Glowing Frame
    pad = 35
    draw.rounded_rectangle([pad, pad, width - pad, height - pad], radius=32, outline=(42, 60, 95), width=3)
    draw.rounded_rectangle([pad + 6, pad + 6, width - pad - 6, height - pad - 6], radius=28, outline=(0, 240, 255, 100), width=2)

    # 1. Top Badges & Header
    # Neon Category Badge
    draw.rounded_rectangle([65, 65, 460, 135], radius=16, fill=(0, 240, 255, 30), outline=(0, 240, 255), width=2)
    draw.text((85, 82), f"⚡ {category_title.upper()}", font=font_badge, fill=(0, 240, 255))

    # Live Date Badge
    today_str = datetime.now().strftime("%d %b %Y | DAILY")
    draw.rounded_rectangle([width - 360, 65, width - 65, 135], radius=16, fill=(255, 255, 255, 15), outline=(100, 120, 160), width=1)
    draw.text((width - 335, 85), today_str, font=font_tag, fill=(190, 210, 240))

    # Brand Title Strip
    draw.text((65, 170), "CULTYNEX MEDIA NETWORK • BREAKING RADAR", font=font_brand, fill=(255, 0, 128))
    draw.line([(65, 210), (width - 65, 210)], fill=(45, 60, 90), width=2)

    # 2. Hero Headline Box (High Contrast Glassmorphism)
    draw.rounded_rectangle([65, 240, width - 65, 490], radius=24, fill=(18, 25, 45, 255), outline=(0, 240, 255), width=2)
    
    # Red "URGENT FOCUS" pill inside headline box
    draw.rounded_rectangle([95, 265, 290, 310], radius=10, fill=(255, 45, 85), outline=(255, 100, 140), width=1)
    draw.text((110, 273), "PRIORITY PULSE", font=font_tag, fill=(255, 255, 255))

    # Wrapped Large Headline
    words = headline.split()
    line1, line2 = "", ""
    for w in words:
        if len(line1 + " " + w) < 32:
            line1 += (" " if line1 else "") + w
        else:
            line2 += (" " if line2 else "") + w
            
    draw.text((95, 335), line1, font=font_headline, fill=(255, 255, 255))
    if line2:
        draw.text((95, 395), line2[:40], font=font_headline, fill=(240, 245, 255))

    # 3. Two Detailed High-Value Bullet Cards
    # Card 1: Tech / Infra Focus
    draw.rounded_rectangle([65, 520, width - 65, 690], radius=20, fill=(16, 22, 38, 255), outline=(60, 85, 130), width=2)
    draw.rounded_rectangle([85, 540, 260, 580], radius=8, fill=(0, 240, 255, 35))
    draw.text((95, 547), "KEY SIGNAL 01", font=font_tag, fill=(0, 240, 255))
    
    # Body text wrapping
    b1_words = bullet_one.split()
    b1_l1, b1_l2 = "", ""
    for w in b1_words:
        if len(b1_l1 + " " + w) < 55:
            b1_l1 += (" " if b1_l1 else "") + w
        else:
            b1_l2 += (" " if b1_l2 else "") + w
    draw.text((85, 595), b1_l1, font=font_bullet_body, fill=(235, 240, 250))
    if b1_l2:
        draw.text((85, 635), b1_l2[:65], font=font_bullet_body, fill=(200, 215, 235))

    # Card 2: Market / Economic Focus
    draw.rounded_rectangle([65, 720, width - 65, 890], radius=20, fill=(16, 22, 38, 255), outline=(60, 85, 130), width=2)
    draw.rounded_rectangle([85, 740, 260, 780], radius=8, fill=(255, 180, 0, 35))
    draw.text((95, 747), "KEY SIGNAL 02", font=font_tag, fill=(255, 190, 40))
    
    b2_words = bullet_two.split()
    b2_l1, b2_l2 = "", ""
    for w in b2_words:
        if len(b2_l1 + " " + w) < 55:
            b2_l1 += (" " if b2_l1 else "") + w
        else:
            b2_l2 += (" " if b2_l2 else "") + w
    draw.text((85, 795), b2_l1, font=font_bullet_body, fill=(235, 240, 250))
    if b2_l2:
        draw.text((85, 835), b2_l2[:65], font=font_bullet_body, fill=(200, 215, 235))

    # 4. Footer & Socials
    draw.line([(65, 930), (width - 65, 930)], fill=(45, 60, 90), width=2)
    draw.text((65, 960), "COMMUTER RADAR • DELHI METRO & RAILWAY NETWORK", font=font_footer, fill=(140, 160, 195))
    draw.text((width - 320, 960), "TELEGRAM: @cultynex_daily", font=font_footer, fill=(0, 240, 255))

    image.save("cultynex_instagram_post.png", "PNG", quality=95)
    
    out_buffer = BytesIO()
    image.convert("RGB").save(out_buffer, format="JPEG", quality=95)
    out_buffer.seek(0)
    return out_buffer

# --- 2. TELEGRAM API METHODS ---
def send_telegram_photo(image_bytes, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("post.jpg", image_bytes, "image/jpeg")}
    data = {
        "chat_id": CHANNEL_CHAT_ID,
        "caption": caption,
        "parse_mode": "HTML"
    }
    return requests.post(url, data=data, files=files).json()

def send_telegram_quiz(q):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPoll"
    payload = {
        "chat_id": CHANNEL_CHAT_ID,
        "question": q["question"][:300],
        "options": json.dumps(q["options"][:4]),
        "type": "quiz",
        "correct_option_id": q["correct_option_id"],
        "explanation": q.get("explanation", "")[:200],
        "is_anonymous": False
    }
    return requests.post(url, data=payload).json()

# --- 3. DYNAMIC CONTENT GENERATION WITH RETRY ---
def generate_commuter_content():
    client = genai.Client(api_key=GEMINI_KEY)
    
    prompt = """
    You are the Executive Editor of CULTYNEX MEDIA, creating high-impact morning news briefs and quizzes for Indian commuters in Delhi-NCR.
    
    Return STRICT JSON (No backticks, no markdown fence):
    {
      "category": "TECH & CAPITAL",
      "headline": "India AI GPU Compute Tender Accelerates Domestic Tech Fab Hubs",
      "bullet_one": "Govt approves ₹10,372 Cr GPU compute tender with 18 high-profile global and domestic bids.",
      "bullet_two": "Quick-commerce tech hubs scale instant 10-minute electronics deliveries along key metro lines.",
      "brief_html": "⚡ <b>CULTYNEX COMMUTER PULSE</b> | <i>Executive Edition</i>\\n━━━━━━━━━━━━━━━━━━━━━\\n\\n🚀 <b>BREAKING: TECH & CAPITAL</b>\\n• <b>India AI Mission:</b> ₹10,372 Cr GPU compute tender gets 18 bids from top domestic & global cloud leaders.\\n• <b>Quick Commerce:</b> 10-minute electronics deliveries expand across NCR corridors.\\n\\n📊 <b>MARKET & LIQUIDITY WATCH</b>\\n• <b>Nifty 50:</b> Sustaining strength near key resistance levels.\\n• <b>24K Gold:</b> ₹79,400 / 10g (Steady festive accumulation).\\n• <b>Bitcoin:</b> Firm above $105,000 baseline.\\n\\n🧠 <b>VOCABULARY ON THE GO</b>\\n• <b>Equanimity</b> [संयम / शांत मन]\\n<i>\"Maintain equanimity during the busiest metro transfers.\"</i>\\n\\n━━━━━━━━━━━━━━━━━━━━━\\n🎯 <b>COMMUTE CHALLENGE (5 RAPID-FIRE QUIZZES)</b>\\n<i>Test your market & tech speed before your station arrives! 👇</i>",
      "quizzes": [
        {
          "question": "🧠 Vocab: What is the accurate meaning of 'Equanimity'?",
          "options": ["Calmness & mental balance under stress", "Severe anxiety", "Fast movement", "Pride"],
          "correct_option_id": 0,
          "explanation": "'Equanimity' means mental calmness, even during difficult moments."
        },
        {
          "question": "🪙 Gold: Hallmark 916 signifies which Karat standard?",
          "options": ["22 Karat", "24 Karat", "18 Karat", "14 Karat"],
          "correct_option_id": 0,
          "explanation": "Hallmark 916 represents 91.6% pure gold, which is 22 Karat."
        },
        {
          "question": "🚆 Metro Trivia: Which Delhi Metro line was the first driverless route in India?",
          "options": ["Magenta Line", "Yellow Line", "Blue Line", "Red Line"],
          "correct_option_id": 0,
          "explanation": "The Magenta Line initiated unattended driverless train operations in India."
        },
        {
          "question": "💻 Tech: What hardware component handles large-scale AI matrix calculations?",
          "options": ["GPU (Graphics Processing Unit)", "Optical Disc Drive", "Hard Disk Drive", "Modem"],
          "correct_option_id": 0,
          "explanation": "GPUs and TPUs specialize in high-speed parallel tensor computations."
        },
        {
          "question": "₿ Crypto: Bitcoin's maximum hard-coded total supply is capped at:",
          "options": ["21 Million", "100 Million", "10 Million", "Unlimited"],
          "correct_option_id": 0,
          "explanation": "Bitcoin has a hard mathematical cap of exactly 21,000,000 coins."
        }
      ]
    }
    """
    
    for attempt in range(1, 4):
        try:
            print(f"API Attempt {attempt} to fetch editorial content...")
            res = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt
            )
            raw = res.text.strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            return json.loads(raw.strip())
        except Exception as e:
            print(f"Attempt {attempt} encountered: {e}")
            if attempt < 3:
                time.sleep(5)
            else:
                raise e

def main():
    print("Initiating Cultynex Multi-Platform Publishing Engine...")
    data = generate_commuter_content()

    print("Generating High-Fidelity 1080x1080 Editorial Card...")
    card_bytes = generate_cinematic_card(
        data.get("category", "TECH & CAPITAL"),
        data.get("headline", "Global & National Tech Pulse"),
        data.get("bullet_one", "Infrastructure & Tech expansion accelerating"),
        data.get("bullet_two", "Market liquidity remains robust across key asset classes")
    )

    print("Publishing to Telegram channel @cultynex_daily...")
    send_telegram_photo(card_bytes, data["brief_html"])
    time.sleep(2)

    print(f"Deploying {len(data['quizzes'])} live interactive quizzes...")
    for idx, q in enumerate(data["quizzes"], 1):
        resp = send_telegram_quiz(q)
        print(f"[{idx}] Quiz Poll live: {resp.get('ok')}")
        time.sleep(2)

    print("Pipeline completed successfully! Instagram asset exported.")

if __name__ == "__main__":
    main()
