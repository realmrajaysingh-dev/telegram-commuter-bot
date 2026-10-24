import os
import json
import time
import requests
from io import BytesIO
from datetime import datetime
from PIL import Image, ImageDraw
from google import genai

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_CHAT_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

# --- 1. ULTRA HD MODERN CARD GENERATOR (IG & TELEGRAM READY: 1080x1080) ---
def generate_cinematic_card(category_title, headline, bullet_one, bullet_two):
    width, height = 1080, 1080
    image = Image.new("RGBA", (width, height), (10, 14, 23, 255))
    draw = ImageDraw.Draw(image)

    # Ambient Dark Tech Gradient
    for y in range(height):
        ratio = y / height
        r = int(10 + (22 - 10) * ratio)
        g = int(14 + (28 - 14) * ratio)
        b = int(23 + (55 - 23) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

    # Glassmorphic Borders
    pad = 40
    draw.rounded_rectangle([pad, pad, width - pad, height - pad], radius=40, outline=(38, 55, 88), width=3)
    draw.rounded_rectangle([pad + 8, pad + 8, width - pad - 8, height - pad - 8], radius=34, outline=(0, 229, 255, 120), width=2)

    # Glowing Category Badge
    badge_x1, badge_y1, badge_x2, badge_y2 = 80, 85, 480, 155
    draw.rounded_rectangle([badge_x1, badge_y1, badge_x2, badge_y2], radius=18, fill=(16, 36, 64, 255), outline=(0, 229, 255), width=2)
    draw.text((115, 105), f"⚡ {category_title.upper()}", fill=(0, 240, 255))

    # Date Header
    today_str = datetime.now().strftime("%d %b %Y | IST")
    draw.text((width - 320, 108), today_str, fill=(140, 160, 195))

    # Brand Title
    draw.text((80, 200), "CULTYNEX MEDIA NETWORK", fill=(0, 229, 255))
    draw.line([(80, 235), (width - 80, 235)], fill=(40, 58, 90), width=2)

    # Main Headline
    draw.rounded_rectangle([80, 270, width - 80, 480], radius=24, fill=(18, 25, 42, 255), outline=(60, 85, 130), width=2)
    draw.text((110, 305), "BREAKING FOCUS:", fill=(255, 75, 145))
    draw.text((110, 360), headline[:85], fill=(255, 255, 255))
    if len(headline) > 85:
        draw.text((110, 415), headline[85:170], fill=(255, 255, 255))

    # Key Insights Section
    draw.text((80, 530), "KEY INSIGHTS & SIGNALS:", fill=(170, 190, 225))
    
    # Bullet Card 1
    draw.rounded_rectangle([80, 580, width - 80, 710], radius=18, fill=(15, 21, 35, 255), outline=(35, 50, 80), width=2)
    draw.text((110, 605), "• POINT 01", fill=(0, 229, 255))
    draw.text((110, 645), bullet_one[:100], fill=(230, 235, 245))

    # Bullet Card 2
    draw.rounded_rectangle([80, 740, width - 80, 870], radius=18, fill=(15, 21, 35, 255), outline=(35, 50, 80), width=2)
    draw.text((110, 765), "• POINT 02", fill=(255, 190, 40))
    draw.text((110, 805), bullet_two[:100], fill=(230, 235, 245))

    # Footer
    draw.line([(80, 930), (width - 80, 930)], fill=(40, 58, 90), width=2)
    draw.text((80, 960), "COMMUTER RADAR • DELHI METRO & RAIL", fill=(120, 145, 180))
    draw.text((width - 340, 960), "@cultynex_daily", fill=(0, 229, 255))

    # Save local file for Instagram export
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
      "headline": "India AI GPU Compute Tender Accelerates Domestic Chip Infrastructure",
      "bullet_one": "Govt approves ₹10,372 Cr GPU compute subsidy to reduce reliance on overseas cloud.",
      "bullet_two": "Quick-commerce tech hubs expand instant electronics deliveries across major metro lines.",
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
                print("Waiting 6 seconds before retrying...")
                time.sleep(6)
            else:
                raise e

def main():
    print("Initiating Cultynex Multi-Platform Publishing Engine...")
    data = generate_commuter_content()

    print("Generating 1080x1080 Cinema Aesthetic Card...")
    card_bytes = generate_cinematic_card(
        data.get("category", "COMMUTER BRIEF"),
        data.get("headline", "Global & National Pulse"),
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
