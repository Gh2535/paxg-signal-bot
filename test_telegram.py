import os
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

message = "✅ تست موفق بود. ربات PAXG Signal Bot به تلگرام وصل شد."

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

response = requests.post(url, data={
    "chat_id": CHAT_ID,
    "text": message
})

print(response.status_code)
print(response.text)
