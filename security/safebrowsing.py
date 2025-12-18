import requests
from typing import Optional
import os
from openai import OpenAI
import json

SAFE_BROWSING_URL = (
    "https://safebrowsing.googleapis.com/v4/threatMatches:find"
)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def check_url_with_google_safe_browsing(url: str) -> Optional[dict]:
    """
    Returns the raw 'matches' dict if the URL is unsafe,
    or None if it appears safe.
    """
    payload = {
        "client": {
            "clientId":    "linkbottle",  # arbitrary string
            "clientVersion": "1.0"
        },
        "threatInfo": {
            "threatTypes": [
                "MALWARE",
                "SOCIAL_ENGINEERING",
                "UNWANTED_SOFTWARE",
                "POTENTIALLY_HARMFUL_APPLICATION",
            ],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url}],
        },
    }

    params = {"key": os.getenv("SAFE_BROWSING_API_KEY")}
    resp = requests.post(SAFE_BROWSING_URL, params=params, json=payload, timeout=5)
    resp.raise_for_status()
    data = resp.json() or {}

    # If there are matches, it's unsafe
    return data.get("matches")

async def classify_url_with_openai(url: str):
    prompt = f"""
        You are a security classifier. Analyze this URL and return ONLY a JSON object.

        URL: {url}

        Categories:
        - safe
        - spam
        - scam_or_phishing
        - extremely_high_risk

        Return ONLY JSON:
        {{
            "category": "...",
            "reason": "..."
        }}
    """

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )

    content = resp.choices[0].message.content
    assert content is not None
    # In JSON mode, this should already be a raw JSON string
    return json.loads(content)