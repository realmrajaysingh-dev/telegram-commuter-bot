import os
import json
import time
import requests
from google import genai

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_CHAT_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
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
    Generate daily commuter content for Indian metro/train passengers in strict JSON format.
    Return JSON format only (no markdown, no backticks):
    {
      "brief": "*🔥 METRO COMMUTER BRIEF 🔥*\\n\\n📈 *Tech & AI:* Key update of today...\\n🪙 *Crypto & Gold:* Latest market sentiment...\\n📚 *Word of the Day:* *Resilient* (Ladhne ki shamta) - Able to recover quickly.\\n\\n━━━━━━━━━━━━━━━━━━━━━━━\\n👇 *Swipe down to attempt today's 5 Daily Challenge Quizzes!*",
      "quizzes": [
        {
          "question": "What is the primary function of Bitcoin Halving?",
          "options": ["Reduce block reward by 50%", "Double transaction fees", "Burn existing coins", "Change encryption"],
          "correct_option_id": 0,
          "explanation": "Bitcoin halving cuts the block reward given to miners by 50% roughly every 4 years."
        },
        {
          "question": "Which word best describes 'existing everywhere simultaneously'?",
          "options": ["Ubiquitous", "Ephemeral", "Pragmatic", "Lucid"],
          "correct_option_id": 0,
          "explanation": "'Ubiquitous' means present, appearing, or found everywhere."
        },
        {
          "question": "In Delhi, 24K gold purity refers to what percentage of gold content?",
          "options": ["99.9%", "91.6%", "75.0%", "85.0%"],
          "correct_option_id": 0,
          "explanation": "24 karat gold is 99.9% pure, whereas 22 karat is 91.6% pure."
        },
        {
          "question": "Which technology allows AI models to process video and audio simultaneously?",
          "options": ["Multimodal AI", "Binary Logic", "Single-core Threading", "Static Compilers"],
          "correct_option_id": 0,
          "explanation": "Multimodal AI can understand and process multiple data types like text, audio, and visual inputs."
        },
        {
          "question": "What is the antonym of 'Frugal'?",
          "options": ["Extravagant", "Economical", "Cautious", "Thrifty"],
          "correct_option_id": 0,
          "explanation": "Frugal means spending money carefully; Extravagant means spending wastefully."
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
    print("Fetching commuter brief and quizzes from Gemini...")
    data = generate_commuter_content()
    
    print("Posting morning brief to channel...")
    send_telegram_message(data["brief"])
    time.sleep(2)
    
    print(f"Posting {len(data['quizzes'])} interactive quizzes...")
    for idx, q in enumerate(data["quizzes"], 1):
        resp = send_telegram_quiz(q)
        print(f"[{idx}] Poll status: {resp.get('ok')}")
        time.sleep(2)
        
    print("Finished! Everything published to @cultynex_daily.")

if __name__ == "__main__":
    main()
