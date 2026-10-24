import os
import json
import time
import requests
from google import genai

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_CHAT_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

def send_telegram_photo(photo_url, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": CHANNEL_CHAT_ID,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML"
    }
    return requests.post(url, data=payload).json()

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
    You are the head editor of a premium, visual-first digital magazine for Indian metro & train commuters.
    Create a punchy, highly aesthetic morning brief and 5 interactive quizzes.
    
    Format requirements for 'brief_html':
    - Bold attention-grabbing headers.
    - Key numbers and stats highlighted.
    - Clear visual line separators.
    - Hinglish friendly, stylish tone.

    Return STRICT JSON (No backticks, no markdown fence):
    {
      "banner_image": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=1200&q=80",
      "brief_html": "⚡ <b>CULTYNEX COMMUTER PULSE</b> | <i>Morning Edition</i>\\n━━━━━━━━━━━━━━━━━━━━━\\n\\n🚀 <b>TECH & AI SNAPSHOT</b>\\n• Tata's Dholera Fab kicks off cleanroom setup.\\n• India's domestic chip supply timeline fast-tracked.\\n\\n📊 <b>MARKET & GOLD RADAR</b>\\n• <b>24K Gold:</b> ₹78,900 / 10g (Wedding season boost)\\n• <b>Bitcoin:</b> Holding strong above $105K institutional floor\\n\\n🧠 <b>VOCABULARY OF THE DAY</b>\\n• <b>Tenacious</b> [दृढ़ / हार न मानने वाला]\\n<i>\"He was tenacious in pursuing his tech startup dreams.\"</i>\\n━━━━━━━━━━━━━━━━━━━━━\\n🎯 <b>TODAY'S COMMUTER CHALLENGE (5 QUIZZES)</b>\\n<i>Tap your choice below to test your speed! 👇</i>",
      "quizzes": [
        {
          "question": "🧠 Vocab: What is the antonym of 'Tenacious'?",
          "options": ["Irresolute / Weak", "Persistent", "Determined", "Stubborn"],
          "correct_option_id": 0,
          "explanation": "'Tenacious' means holding fast; 'Irresolute' means hesitant or giving up easily."
        },
        {
          "question": "🪙 Gold: What does 24 Karat gold signify in purity?",
          "options": ["99.9% Pure Gold", "91.6% Pure Gold", "75.0% Pure Gold", "82.5% Pure Gold"],
          "correct_option_id": 0,
          "explanation": "24K is 99.9% pure, while 22K (hallmark 916) is 91.6% pure."
        },
        {
          "question": "💻 Tech: Tata's major semiconductor chip fab is being built at:",
          "options": ["Dholera, Gujarat", "Noida, UP", "Bengaluru, Karnataka", "Hyderabad, Telangana"],
          "correct_option_id": 0,
          "explanation": "Tata Electronics is constructing its mega chip fabrication unit at Dholera, Gujarat."
        },
        {
          "question": "₿ Crypto: In Bitcoin mining, a 'Halving' event occurs roughly every:",
          "options": ["4 Years (210,000 blocks)", "2 Years", "1 Year", "6 Months"],
          "correct_option_id": 0,
          "explanation": "Halving occurs approximately every 4 years, cutting the block issuance rate by 50%."
        },
        {
          "question": "🚆 Metro Trivia: Which Delhi Metro line was the first to implement fully driverless train operations?",
          "options": ["Magenta Line", "Yellow Line", "Blue Line", "Red Line"],
          "correct_option_id": 0,
          "explanation": "The Magenta Line (Line 8) was the first to introduce unattended driverless train operations."
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
    print("Generating premium commuter briefing...")
    data = generate_commuter_content()
    
    # 1. Post Visual Header + Styled Brief
    print("Posting banner image + brief...")
    img = data.get("banner_image", "[https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=1200&q=80](https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=1200&q=80)")
    send_telegram_photo(img, data["brief_html"])
    time.sleep(2)
    
    # 2. Post Interactive Quizzes
    print(f"Posting {len(data['quizzes'])} polls...")
    for idx, q in enumerate(data["quizzes"], 1):
        resp = send_telegram_quiz(q)
        print(f"[{idx}] Poll status: {resp.get('ok')}")
        time.sleep(2)
        
    print("Done! Check @cultynex_daily.")

if __name__ == "__main__":
    main()
