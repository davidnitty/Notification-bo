import requests
import time
import logging
import json
import sqlite3
import re
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Your bot token
BOT_TOKEN = "7831036263:AAHSisyLSr5bSwfJ2jGXasRfLcRluo2y5gk"
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

class AddressUtils:
    @staticmethod
    def validate_address(address: str):
        address = address.strip()
        
        # Check io address
        if address.startswith('io') and re.match(r'^io[0-9a-z]{41}$', address):
            return True, address.lower()
        # Check 0x address  
        elif address.startswith('0x') and re.match(r'^0x[0-9a-fA-F]{40}$', address):
            return True, address.lower()
        else:
            return False, None
    
    @staticmethod
    def shorten_address(address: str, length: int = 8) -> str:
        if len(address) <= length * 2:
            return address
        return f"{address[:length]}...{address[-length:]}"

class Storage:
    def __init__(self, db_path: str = "bot_data.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                address TEXT NOT NULL,
                preferences TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    
    def save_user(self, chat_id: int, address: str, is_active: bool = True) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        preferences = json.dumps({"rewards": True, "tx_in": True, "tx_out": True})
        cursor.execute('''
            INSERT OR REPLACE INTO users (chat_id, address, preferences, created_at, is_active)
            VALUES (?, ?, ?, ?, ?)
        ''', (chat_id, address, preferences, datetime.now().isoformat(), is_active))
        conn.commit()
        conn.close()
        logger.info(f"Saved user {chat_id}")
    
    def get_user(self, chat_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id, address, preferences FROM users WHERE chat_id = ?', (chat_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'chat_id': row[0], 'address': row[1], 'preferences': json.loads(row[2])}
        return None
    
    def is_address_in_watchlist(self, address: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM users WHERE LOWER(address) = LOWER(?) AND is_active = TRUE', (address,))
        result = cursor.fetchone() is not None
        conn.close()
        return result

class TelegramBot:
    def __init__(self):
        self.storage = Storage()
    
    def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown") -> bool:
        url = f"{TELEGRAM_API_BASE}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info(f"✅ Message sent to {chat_id}")
                return True
            else:
                logger.error(f"❌ Failed to send message: {response.text}")
                return False
        except Exception as e:
            logger.error(f"❌ Error sending message to {chat_id}: {e}")
            return False
    
    def process_message(self, chat_id: int, text: str) -> None:
        text = text.strip()
        logger.info(f"Processing: '{text}' from {chat_id}")
        
        if text.startswith("/start"):
            self.handle_start(chat_id)
        elif text.startswith("/setaddress"):
            self.handle_set_address(chat_id, text)
        elif text.startswith("/getaddress"):
            self.handle_get_address(chat_id)
        elif text.startswith("/unsubscribe"):
            self.handle_unsubscribe(chat_id)
        elif text.startswith("/help"):
            self.handle_help(chat_id)
        elif text.startswith("/watchlist"):
            self.handle_watchlist(chat_id)
        else:
            # Check if it's an address
            is_valid, normalized_address = AddressUtils.validate_address(text)
            if is_valid:
                self.handle_address_input(chat_id, text)
            else:
                self.send_message(chat_id, "❓ Unknown command or invalid address. Use /help for commands.")
    
    def handle_start(self, chat_id: int) -> None:
        welcome = """
🤖 *IoTeX Alerts Bot*

I monitor your IoTeX address for:
• 🎉 Staking rewards
• 📥 Incoming transactions  
• 📤 Outgoing transactions

*Quick start:* Send me your IoTeX address!

*Supported formats:*
• `io1abc...xyz` (IoTeX native)
• `0xabc...xyz` (EVM)

*Commands:*
`/help` - Show all commands
        """
        self.send_message(chat_id, welcome)
    
    def handle_address_input(self, chat_id: int, address: str) -> None:
        is_valid, normalized_address = AddressUtils.validate_address(address)
        
        if not is_valid:
            self.send_message(chat_id, "❌ Invalid format. Use: `io1...` or `0x...`")
            return
        
        if self.storage.is_address_in_watchlist(normalized_address):
            short_addr = AddressUtils.shorten_address(normalized_address)
            self.send_message(chat_id, f"⚠️ Already in watchlist:\n`{short_addr}`")
            return
        
        self.storage.save_user(chat_id, normalized_address)
        short_addr = AddressUtils.shorten_address(normalized_address)
        
        response = f"""
✅ *Added to Watchlist!*

`{short_addr}`

I'll monitor this address for:
• Staking rewards 🎉
• Incoming TX 📥  
• Outgoing TX 📤

Use `/watchlist` to view your address.
        """
        self.send_message(chat_id, response)
    
    def handle_set_address(self, chat_id: int, text: str) -> None:
        parts = text.split()
        if len(parts) < 2:
            self.send_message(chat_id, "📍 Send: `/setaddress 0x1b0394CAd46b8745Cc4AbE2553243aD0170DA623`")
            return
        self.handle_address_input(chat_id, parts[1])
    
    def handle_watchlist(self, chat_id: int) -> None:
        user = self.storage.get_user(chat_id)
        if not user:
            self.send_message(chat_id, "📋 No addresses. Send an address to start!")
            return
        short_addr = AddressUtils.shorten_address(user['address'])
        self.send_message(chat_id, f"📋 *Your Watchlist*\n\n✅ `{short_addr}`")
    
    def handle_get_address(self, chat_id: int) -> None:
        user = self.storage.get_user(chat_id)
        if not user:
            self.send_message(chat_id, "❌ No address set. Send an address!")
            return
        short_addr = AddressUtils.shorten_address(user['address'])
        self.send_message(chat_id, f"📍 `{short_addr}`")
    
    def handle_unsubscribe(self, chat_id: int) -> None:
        user = self.storage.get_user(chat_id)
        if user:
            self.storage.save_user(chat_id, user['address'], False)
        self.send_message(chat_id, "🔔 Unsubscribed. Send an address to resubscribe.")
    
    def handle_help(self, chat_id: int) -> None:
        help_text = """
🆘 *Commands*

`/start` - Welcome message
`/setaddress <addr>` - Add address  
`/watchlist` - View your address
`/getaddress` - Show address
`/unsubscribe` - Stop alerts
`/help` - This message

*Just send an address* to quickly add it!
        """
        self.send_message(chat_id, help_text)

def get_bot_info():
    """Check if bot token is valid"""
    url = f"{TELEGRAM_API_BASE}/getMe"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get('ok'):
            bot_info = data['result']
            logger.info(f"✅ Bot found: @{bot_info['username']} ({bot_info['first_name']})")
            return True
        else:
            logger.error(f"❌ Invalid bot token: {data}")
            return False
    except Exception as e:
        logger.error(f"❌ Error checking bot: {e}")
        return False

def poll_updates():
    """Poll for Telegram updates"""
    bot = TelegramBot()
    offset = None
    
    logger.info("🤖 Starting IoTeX Alert Bot (Polling Mode)...")
    
    # First, check if bot token is valid
    if not get_bot_info():
        logger.error("Please check your bot token with @BotFather")
        return
    
    logger.info("✅ Bot is ready! Send a message to your bot on Telegram...")
    
    while True:
        try:
            url = f"{TELEGRAM_API_BASE}/getUpdates"
            params = {'timeout': 30}
            if offset:
                params['offset'] = offset
                
            response = requests.get(url, params=params, timeout=35)
            data = response.json()
            
            if data.get('ok'):
                for update in data['result']:
                    offset = update['update_id'] + 1
                    
                    if 'message' in update:
                        message = update['message']
                        chat_id = message['chat']['id']
                        text = message.get('text', '').strip()
                        
                        if text:  # Only process non-empty messages
                            logger.info(f"📨 From {chat_id}: {text}")
                            bot.process_message(chat_id, text)
            
            time.sleep(1)
            
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            logger.error(f"Error in polling: {e}")
            time.sleep(5)

if __name__ == '__main__':
    poll_updates()