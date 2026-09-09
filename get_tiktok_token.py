import os
import requests
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv("TIKTOK_APP_ID")
APP_SECRET = os.getenv("TIKTOK_APP_SECRET")

AUTH_CODE = input("Paste auth_code: ").strip()

url = "https://business-api.tiktok.com/open_api/v1.3/oauth2/access_token/"

payload = {
    "app_id": APP_ID,
    "secret": APP_SECRET,
    "auth_code": AUTH_CODE
}

response = requests.post(
    url,
    headers={"Content-Type": "application/json"},
    json=payload
)

data = response.json()

print(data)