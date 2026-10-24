import os
import json
import time
import requests
from io import BytesIO
from google import genai
from google.genai import types

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_CHAT_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

def send_telegram_photo(image_bytes, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("infographic.jpg", image_bytes, "image/jpeg")}
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

def generate_ai_artwork(client, visual_prompt):
    print("Generating 8K Cinematic Infographic Poster via Imagen 3...")
    result = client.models.generate_images(
        model='imagen-3.0-generate-002',
        prompt=visual_prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio="1:1"
        )
    )
    generated_image = result.generated_images[0]
    img_bytes = generated_image.image.image_bytes
    
    # Save local file for Instagram export
    with open("cultynex_instagram_post.png", "wb") as f:
        f.write(img_bytes)
        
    return BytesIO(img_bytes)

def generate_commuter_content():
    client = genai.Client(api_key=GEMINI_KEY)
    
    prompt = """
    You are the Senior Creative Director for CULTYNEX MEDIA NETWORK.
    Produce an elite, mind-blowing educational brief & quizzes for high-curiosity commuters and creators.
    Also generate an ultra-detailed cinematic art prompt for Imagen 3 matching the aesthetic of a futuristic sci-fi cosmic blueprint or quantum computing schematic.
    
    Return STRICT JSON (No backticks, no markdown fence):
    {
      "visual_prompt": "Ultra-detailed futuristic scientific infographic poster, dark cosmic blue and neon orange glowing accents, cross-section breakdown of quantum AI computing architecture and universe scale, intricate geometric diagrams, blueprint HUD overlays, glowing telemetry data panels, hyper-realistic, 8k resolution, cinematic lighting, masterpiece aesthetic.",
      "brief_html": "🌌 <b>CULTYNEX COSMIC & TECH RADAR</b> | <i>Deep-Dive Edition</i>\\n━━━━━━━━━━━━━━━━━━━━━\\n\\n⚡ <b>DEEP TECH BREAKTHROUGH</b>\\n• <b>Quantum-AI Synergy:</b> Next-gen neural accelerators bridge silicon micro-architecture with optical tensor meshes.\\n• <b>Compute Expansion:</b> ₹10,372 Cr GPU infra cluster fast-tracks indigenous foundational models.\\n\\n📊 <b>MACRO & CAPITAL MONITOR</b>\\n• <b>Bitcoin:</b> Consolidating institutional liquidity above $105,000.\\n• <b>Physical Gold:</b> ₹79,400 / 10g holding historic macro highs.\\n\\n🧠 <b>CONCEPT OF THE DAY</b>\\n• <b>Singularity:</b> The theoretical threshold where algorithmic intelligence surpasses total human cognitive capability.\\n━━━━━━━━━━━━━━━━━━━━━\\n🎯 <b>SPEED CHALLENGE (5 RAPID-FIRE QUIZZES)</b>\\n<i>Test your intellect before the next transit stop! 👇</i>",
      "quizzes": [
        {
          "question": "⚛️ Physics: What region marks the point of no return around a Black Hole?",
          "options": ["Event Horizon", "Accretion Disk", "Singularity", "Photon Sphere"],
          "correct_option_id": 0,
          "explanation": "The Event Horizon is the gravitational boundary from which nothing, not even light, can escape."
        },
        {
          "question": "💻 Computing: What is the fundamental unit of quantum information?",
          "options": ["Qubit", "Byte", "Transistor", "Pixel"],
          "correct_option_id": 0,
          "explanation": "A qubit (quantum bit) is the basic unit of information in quantum computing."
        },
        {
          "question": "🪙 Gold: Hallmark 916 purity corresponds to:",
          "options": ["22 Karat (91.6% Pure)", "24 Karat (99.9% Pure)", "18 Karat (75.0% Pure)", "14 Karat"],
          "correct_option_id": 0,
          "explanation": "Hallmark 916 means 91.6% pure gold, designated as 22 Karat."
        },
        {
          "question": "🚆 Metro: Which metro system in India operates the longest network route?",
          "options": ["Delhi Metro (DMRC)", "Namma Metro Bengaluru", "Mumbai Metro", "Kolkata Metro"],
          "correct_option_id": 0,
          "explanation": "Delhi Metro has the largest operational network spanning over 390+ kilometers."
        },
        {
          "question": "🚀 Space: What theoretical passage creates shortcuts through spacetime?",
          "options": ["Einstein-Rosen Bridge (Wormhole)", "Kuiper Belt", "Oort Cloud", "Dyson Sphere"],
          "correct_option_id": 0,
          "explanation": "An Einstein-Rosen bridge is a speculative structure linking disparate points in spacetime."
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
            data = json.loads(raw.strip())
            return client, data
        except Exception as e:
            print(f"Attempt {attempt} failed: {e}")
            if attempt < 3:
                time.sleep(5)
            else:
                raise e

def main():
    print("Initiating Cultynex Next-Gen Publishing Engine...")
    client, data = generate_commuter_content()

    # Generate 8K AI Infographic Poster
    art_stream = generate_ai_artwork(client, data["visual_prompt"])

    # Publish to Telegram
    print("Publishing to Telegram channel @cultynex_daily...")
    send_telegram_photo(art_stream, data["brief_html"])
    time.sleep(2)

    # Publish Quizzes
    print(f"Deploying {len(data['quizzes'])} live interactive quizzes...")
    for idx, q in enumerate(data["quizzes"], 1):
        resp = send_telegram_quiz(q)
        print(f"[{idx}] Quiz Poll live: {resp.get('ok')}")
        time.sleep(2)

    print("Complete! 8K Poster and quizzes published. Instagram asset exported.")

if __name__ == "__main__":
    main()
