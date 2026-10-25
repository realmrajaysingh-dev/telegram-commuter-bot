def fetch_tech_dispatch():
    """Gemini generates editorial content, verified metrics, and simulated X quote."""
    # Find working model dynamically
    model_name = "gemini-1.5-flash"
    try:
        models = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
        for candidate in ["models/gemini-1.5-flash", "models/gemini-1.5-pro", "models/gemini-pro"]:
            if candidate in models:
                model_name = candidate
                break
        else:
            if models:
                model_name = models[0]
    except Exception:
        model_name = "gemini-pro"

    model = genai.GenerativeModel(model_name)
    
    prompt = """
    You are the Lead Tech & Macro Strategist for CULTYNEX MEDIA NETWORK.
    Generate a high-authority editorial news brief on a major trending Tech/Silicon/AI breakthrough.

    OUTPUT ONLY VALID JSON:
    {
      "tag": "CULTYNEX DISPATCH",
      "read_time": "2 MIN READ",
      "headline": "PUNCHY EDITORIAL HEADLINE IN UPPERCASE (MAX 10 WORDS)",
      "image_keyword": "technology microchip",
      "tweet_author": "Tech Analyst",
      "tweet_handle": "@TechAnalyst",
      "tweet_text": "One concise, hard-hitting analytical reaction on market or architecture impact.",
      "metric_val": "+35%",
      "metric_label": "ENERGY EFFICIENCY | 2026 ROADMAP",
      "telegram_body": "3 crisp editorial bullet points explaining The Big Picture, Key Numbers, and Why It Matters.",
      "quiz_question": "One sharp single-choice question based on this event.",
      "options": ["Correct option", "Wrong option 1", "Wrong option 2", "Wrong option 3"],
      "correct_id": 0
    }
    """
    
    res = model.generate_content(prompt)
    text = res.text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(text)
