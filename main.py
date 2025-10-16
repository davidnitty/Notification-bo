import os
import requests
import logging
import time

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Your bot token
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '7831036263:AAHSisyLSr5bSwfJ2jGXasRfLcRluo2y5gk')
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TOKEN}"

def get_updates(offset=None):
    try:
        url = f"{TELEGRAM_API_URL}/getUpdates"
        params = {"timeout": 100, "offset": offset}
        response = requests.get(url, params=params, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f"Error getting updates: {e}")
        return {"ok": False}

def send_message(chat_id, text):
    try:
        url = f"{TELEGRAM_API_URL}/sendMessage"
        data = {"chat_id": chat_id, "text": text}
        response = requests.post(url, json=data, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return {"ok": False}

def main():
    logger.info("Starting Telegram bot with direct API...")
    last_update_id = None
    
    while True:
        try:
            updates = get_updates(offset=last_update_id)
            
            if updates.get("ok"):
                for update in updates.get("result", []):
                    last_update_id = update["update_id"] + 1
                    
                    if "message" in update:
                        message = update["message"]
                        chat_id = message["chat"]["id"]
                        text = message.get("text", "")
                        
                        if text == "/start":
                            send_message(chat_id, "Hello! Bot is running on Railway!")
                        elif text == "/check":
                            send_message(chat_id, "Checking transactions...")
                        else:
                            send_message(chat_id, f"You said: {text}")
            
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(5)

if __name__ == '__main__':
    main()