import os
import json
import time
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from google import genai

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_CHAT_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

def create_premium_banner(title_text="CULTYNEX DAILY PULSE", subtitle_text="METRO COMMUTER EDITION"):
    # 1200x630 Premium Aspect Ratio Card
    width, height = 1200, 550
    base = Image.new("RGBA", (width, height), (13, 17, 23, 255)) # Dark base
    draw = ImageDraw.Draw(base)

    # 1. Subtle Neon Mesh Gradient Background
    for y in range(height):
        r = int(18 + (35 - 18) * (y / height))
        g = int(22 + (20 - 22) * (y / height))
        b = int(40 + (75 - 40) * (y / height))
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

    # 2. Outer Glow & Rounded Card Frame
    card_margin = 25
    card_box = [card_margin, card_margin, width - card_margin, height - card_margin]
    
    # Outer Border with Cyberpunk Gradient accent
    draw.rounded_rectangle(card_box, radius=32, outline=(65, 90, 150, 200), width=3)
    
    # Accent glowing corner indicators
    accent_color = (0, 225, 255, 240) # Electric cyan
    purple_accent = (255, 0, 128, 220) # Hot pink/purple
    
    # Decorative top badge
    draw.rounded_rectangle([60, 60, 290, 105], radius=15, fill=(30, 42, 65, 255), outline=accent_color, width=2)
    draw.text((80, 72), "⚡ LIVE BRIEF", fill=accent_color)

    # 3. Typography
    draw.text((65, 150), title_text, fill=(255, 255, 255))
    draw.text((65, 230), subtitle_text, fill=(160, 180, 220))
    
    # Bottom aesthetic bar
    draw.line([(65, 450), (width - 65, 450)], fill=(45, 55, 80), width=2)
    draw.text((65, 470), "DELHI METRO & RAILWAY TRANSIT BRIEF", fill=purple_accent)
    draw.text((width - 320, 470), "DAILY MORNING & EVENING", fill=(130, 145, 175))

    output = BytesIO()
    base.convert("RGB").save(output, format="JPEG", quality=95)
    output.seek(0)
    return output

def send_telegram_photo(image_bytes, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("card.jpg", image_bytes, "image/jpeg")}
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

def generate_commuter_content():
    client = genai.Client(api_key=GEMINI_KEY)
    prompt = """
    Create a crisp, stylish, high-energy morning news brief for Delhi NCR commuters.
    Return STRICT JSON (No backticks, no markdown fence):
    {
      "card_title": "CULTYNEX COMMUTER PULSE",
      "card_sub": "MORNING RUSH EDITION • TECH & ASSETS",
      "brief_html": "⚡ <b>CULTYNEX COMMUTER PULSE</b> | <i>Morning Rush Edition</i>\\n━━━━━━━━━━━━━━━━━━━━━\\n\\n🚀 <b>TECH & AI RADAR</b>\\n• <b>India AI Mission:</b> ₹10,372 Cr GPU compute tender gets massive bids from leading cloud players.\\n• <b>Quick-Commerce Wars:</b> 10-minute gadget deliveries roll out in major metro hubs.\\n\\n📈 <b>MARKET & ASSETS CHECK</b>\\n• <b>Nifty 50:</b> Eyeing key resistance near 24,350.\\n• <b>24K Gold:</b> ₹79,200 / 10g (Festive & wedding demand solid).\\n• <b>Bitcoin:</b> Steady accumulation above $104,800.\\n\\n🧠 <b>VOCAB ON THE MOVE</b>\\n• <b>Equanimity</b> [संयम / मानसिक स्थिरता]\\n<i>\"Maintain equanimity even jab metro interchange pe heavy rush ho.\"</i>\\n\\n🚆 <b>URBAN TRANSIT NOTE</b>\\n• <b>Namo Bharat:</b> Rapid RRTS corridors cutting commute times drastically across Delhi-NCR.\\n━━━━━━━━━━━━━━━━━━━━━\\n🎯 <b>COMMUTE CHALLENGE (5 RAPID-FIRE QUIZZES)</b>\\n<i>Test your street-smart IQ before your station arrives! 👇</i>",
      "quizzes": [
        {
          "question": "🧠 Vocab: What is the primary meaning of 'Equanimity'?",
          "options": ["Mental calmness in difficult situations", "Extreme excitement", "Physical endurance", "Carelessness"],
          "correct_option_id": 0,
          "explanation": "'Equanimity' means composure and calmness, especially under stress."
        },
        {
          "question": "🪙 Gold: Hallmark 916 gold is equivalent to which karat purity?",
          "options": ["22 Karat", "24 Karat", "18 Karat", "20 Karat"],
          "correct_option_id": 0,
          "explanation": "916 hallmark indicates 91.6% purity, which is 22 Karat gold."
        },
        {
          "question": "🚆 Metro: What does RRTS stand for in India's regional rapid transit?",
          "options": ["Regional Rapid Transit System", "Railway Rapid Track Service", "Road Railway Transport Scheme", "Regional Rail Tech Standard"],
          "correct_option_id": 0,
          "explanation": "RRTS stands for Regional Rapid Transit System, powering Namo Bharat corridors."
        },
        {
          "question": "💻 Tech: What hardware component handles large-scale AI matrix training?",
          "options": ["GPU (Graphics Processing Unit)", "HDD (Hard Disk Drive)", "Sound Card", "Modem"],
          "correct_option_id": 0,
          "explanation": "GPUs and TPUs specialize in parallel matrix operations required for AI neural networks."
        },
        {
          "question": "₿ Crypto: What consensus mechanism does the Bitcoin network rely on?",
          "options": ["Proof of Work (PoW)", "Proof of Stake (PoS)", "Delegated Authority", "Proof of History"],
          "correct_option_id": 0,
          "explanation": "Bitcoin operates on the Proof of Work (PoW) consensus mechanism with SHA-256."
        }
      ]
    }
    """
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

def main():
    print("Generating commuter content...")
    data = generate_commuter_content()
    
    print("Designing neon-aesthetic commuter card...")
    banner_img = create_premium_banner(
        data.get("card_title", "CULTYNEX DAILY PULSE"),
        data.get("card_sub", "MORNING COMMUTER EDITION")
    )
    
    print("Sending card + brief to @cultynex_daily...")
    send_telegram_photo(banner_img, data["brief_html"])
    time.sleep(2)
    
    print(f"Posting {len(data['quizzes'])} polls...")
    for idx, q in enumerate(data["quizzes"], 1):
        resp = send_telegram_quiz(q)
        print(f"[{idx}] Poll status: {resp.get('ok')}")
        time.sleep(2)
        
    print("Pipeline finished successfully!")

if __name__ == "__main__":
    main()
