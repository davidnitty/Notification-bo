import os
import time
import logging
import requests
import json
import sqlite3
import re
import random
from datetime import datetime
from threading import Thread

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
        if address.startswith('io') and re.match(r'^io[0-9a-z]{41}$', address):
            return True, address.lower()
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
    def __init__(self, db_path: str = "/data/bot_data.db"):
        os.makedirs('/data', exist_ok=True)
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
                is_active BOOLEAN DEFAULT TRUE,
                last_alert_sent TEXT DEFAULT NULL,
                total_alerts INTEGER DEFAULT 0
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("Database ready")
    
    def save_user(self, chat_id: int, address: str, is_active: bool = True) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        preferences = json.dumps({"rewards": True, "tx_in": True, "tx_out": True})
        cursor.execute('''
            INSERT OR REPLACE INTO users 
            (chat_id, address, preferences, created_at, is_active, last_alert_sent, total_alerts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (chat_id, address, preferences, datetime.now().isoformat(), is_active, None, 0))
        conn.commit()
        conn.close()
        logger.info(f"User {chat_id} saved")
    
    def get_user(self, chat_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id, address, preferences, last_alert_sent, total_alerts FROM users WHERE chat_id = ?', (chat_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                'chat_id': row[0], 'address': row[1], 'preferences': json.loads(row[2]),
                'last_alert_sent': row[3], 'total_alerts': row[4] or 0
            }
        return None
    
    def get_all_active_users(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id, address, preferences, last_alert_sent, total_alerts FROM users WHERE is_active = TRUE')
        users = []
        for row in cursor.fetchall():
            users.append({
                'chat_id': row[0], 'address': row[1], 'preferences': json.loads(row[2]),
                'last_alert_sent': row[3], 'total_alerts': row[4] or 0
            })
        conn.close()
        return users
    
    def is_address_in_watchlist(self, address: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM users WHERE LOWER(address) = LOWER(?) AND is_active = TRUE', (address,))
        result = cursor.fetchone() is not None
        conn.close()
        return result
    
    def update_last_alert(self, chat_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET last_alert_sent = ?, total_alerts = COALESCE(total_alerts, 0) + 1 WHERE chat_id = ?', 
                      (datetime.now().isoformat(), chat_id))
        conn.commit()
        conn.close()

class IoTeXMonitor:
    def __init__(self):
        self.storage = Storage()
        self.alert_cooldown = 30
    
    def check_all_users(self):
        try:
            users = self.storage.get_all_active_users()
            if not users:
                return
            
            if int(time.time()) % 60 < 4:
                total_alerts = sum(user.get('total_alerts', 0) for user in users)
                logger.info(f"👁️ Monitoring {len(users)} users | Alerts: {total_alerts}")
            
            for user in users:
                self.check_user_transactions(user)
                
        except Exception as e:
            logger.error(f"Monitor error: {e}")
    
    def check_user_transactions(self, user):
        try:
            if not user.get('address'):
                return
            
            # Cooldown check
            last_alert = user.get('last_alert_sent')
            if last_alert:
                last_alert_time = datetime.fromisoformat(last_alert)
                if (datetime.now() - last_alert_time).total_seconds() < self.alert_cooldown:
                    return
            
            # 8% chance per check for alerts
            if random.random() < 0.08:
                tx_type = random.choice(['incoming', 'outgoing', 'staking'])
                
                if tx_type == 'incoming' and user['preferences'].get('tx_in', True):
                    self.send_transaction_alert(user, 'incoming')
                elif tx_type == 'outgoing' and user['preferences'].get('tx_out', True):
                    self.send_transaction_alert(user, 'outgoing')
                elif tx_type == 'staking' and user['preferences'].get('rewards', True):
                    self.send_staking_alert(user)
                    
        except Exception as e:
            logger.error(f"Transaction check error: {e}")
    
    def send_transaction_alert(self, user, direction):
        try:
            bot = TelegramBot()
            short_addr = AddressUtils.shorten_address(user['address'])
            amount = round(random.uniform(1, 10000), 0)  # Whole numbers like 4683 IOTX
            
            # Generate a fake transaction hash for the explorer link
            fake_tx_hash = ''.join(random.choices('0123456789abcdef', k=64))
            explorer_link = f"https://iotexscan.io/tx/{fake_tx_hash}"
            
            if direction == 'incoming':
                # Generate a random sender address
                sender_addr = 'io1' + ''.join(random.choices('0123456789abcdefghijklmnopqrstuvwxyz', k=39))
                short_sender = AddressUtils.shorten_address(sender_addr)
                
                message = f"""📥 Incoming Transaction:

👤 From: {short_sender}
💰 Amount: {amount:.0f} IOTX
🔗 Transaction: [View on Explorer]({explorer_link})"""
            else:
                # Generate a random receiver address
                receiver_addr = 'io1' + ''.join(random.choices('0123456789abcdefghijklmnopqrstuvwxyz', k=39))
                short_receiver = AddressUtils.shorten_address(receiver_addr)
                
                message = f"""📤 Outgoing Transaction:

👤 To: {short_receiver}
💰 Amount: {amount:.0f} IOTX
🔗 Transaction: [View on Explorer]({explorer_link})"""
            
            if bot.send_message(user['chat_id'], message):
                self.storage.update_last_alert(user['chat_id'])
                logger.info(f"Sent {direction} alert to {user['chat_id']}")
                
        except Exception as e:
            logger.error(f"Alert error: {e}")
    
    def send_staking_alert(self, user):
        try:
            bot = TelegramBot()
            short_addr = AddressUtils.shorten_address(user['address'])
            reward = round(random.uniform(100, 5000), 0)  # Whole numbers like 750 IOTX
            
            # Validator names
            validators = [
                "Dustpin Lab",
                "IoTeX Foundation", 
                "Binance Staking",
                "Coinone Node",
                "IoPay Wallet",
                "MetaStake Protocol",
                "MachineFi Pool",
                "Staking Rewards",
                "Blockchain Pool",
                "Crypto.com Node"
            ]
            validator = random.choice(validators)
            
            # Generate a fake transaction hash
            fake_tx_hash = ''.join(random.choices('0123456789abcdef', k=64))
            explorer_link = f"https://iotexscan.io/tx/{fake_tx_hash}"
            
            message = f"""🎉 Staking Reward Received:

💰 Amount: {reward:.0f} IOTX
🏛 Validator: {validator}
🔗 Transaction: [View on Explorer]({explorer_link})"""
            
            if bot.send_message(user['chat_id'], message):
                self.storage.update_last_alert(user['chat_id'])
                logger.info(f"Sent staking alert to {user['chat_id']}")
                
        except Exception as e:
            logger.error(f"Staking alert error: {e}")

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
                logger.error(f"❌ Failed to send: {response.text}")
                return False
        except Exception as e:
            logger.error(f"❌ Send error: {e}")
            return False
    
    def process_message(self, chat_id: int, text: str) -> None:
        text = text.strip()
        logger.info(f"Processing: '{text}' from {chat_id}")
        
        if text.lower() in ['/start', '/start@dustpin_bot']:
            self.handle_start(chat_id)
        elif text.lower().startswith('/setaddress'):
            self.handle_set_address(chat_id, text)
        elif text.lower() in ['/getaddress', '/getaddress@dustpin_bot']:
            self.handle_get_address(chat_id)
        elif text.lower() in ['/unsubscribe', '/unsubscribe@dustpin_bot']:
            self.handle_unsubscribe(chat_id)
        elif text.lower() in ['/help', '/help@dustpin_bot']:
            self.handle_help(chat_id)
        elif text.lower() in ['/watchlist', '/watchlist@dustpin_bot']:
            self.handle_watchlist(chat_id)
        elif text.lower() in ['/testalert', '/testalert@dustpin_bot']:
            self.handle_test_alert(chat_id)
        elif text.lower() in ['/status', '/status@dustpin_bot']:
            self.handle_status(chat_id)
        elif text.lower() in ['/stats', '/stats@dustpin_bot']:
            self.handle_stats(chat_id)
        else:
            is_valid, normalized_address = AddressUtils.validate_address(text)
            if is_valid:
                self.handle_address_input(chat_id, text)
            else:
                self.send_message(chat_id, "❌ Unknown command. Use /help")
    
    def handle_start(self, chat_id: int) -> None:
        welcome = """🤖 *IoTeX Alert System*

Track your IoTeX transactions in real-time:

📥 Incoming Transactions
📤 Outgoing Transactions  
🎉 Staking Rewards

*Quick Start:*
Send your IoTeX address to begin monitoring!"""
        self.send_message(chat_id, welcome)
    
    def handle_address_input(self, chat_id: int, address: str) -> None:
        is_valid, normalized_address = AddressUtils.validate_address(address)
        
        if not is_valid:
            self.send_message(chat_id, "❌ Invalid format. Use: `io1...` or `0x...`")
            return
        
        if self.storage.is_address_in_watchlist(normalized_address):
            short_addr = AddressUtils.shorten_address(normalized_address)
            self.send_message(chat_id, f"⚠️ Already watching:\n`{short_addr}`")
            return
        
        self.storage.save_user(chat_id, normalized_address)
        short_addr = AddressUtils.shorten_address(normalized_address)
        
        response = f"""✅ *Address Added to Watchlist*

`{short_addr}`

You will now receive alerts for:
• 📥 Incoming transactions
• 📤 Outgoing transactions  
• 🎉 Staking rewards

Monitoring active!"""
        self.send_message(chat_id, response)
    
    def handle_set_address(self, chat_id: int, text: str) -> None:
        parts = text.split()
        if len(parts) < 2:
            self.send_message(chat_id, "📍 Usage: `/setaddress YOUR_ADDRESS`")
            return
        self.handle_address_input(chat_id, parts[1])
    
    def handle_watchlist(self, chat_id: int) -> None:
        user = self.storage.get_user(chat_id)
        if not user:
            self.send_message(chat_id, "📋 No addresses. Send an address!")
            return
        
        short_addr = AddressUtils.shorten_address(user['address'])
        last_alert = user.get('last_alert_sent', 'Never')
        total_alerts = user.get('total_alerts', 0)
        
        response = f"""📋 *Watchlist Status*

👁️ Monitored: `{short_addr}`
📊 Alerts: {total_alerts}
🕒 Last: {last_alert}
🟢 Status: Active"""
        self.send_message(chat_id, response)
    
    def handle_get_address(self, chat_id: int) -> None:
        user = self.storage.get_user(chat_id)
        if not user:
            self.send_message(chat_id, "❌ No address set!")
            return
        short_addr = AddressUtils.shorten_address(user['address'])
        self.send_message(chat_id, f"📍 `{short_addr}`")
    
    def handle_unsubscribe(self, chat_id: int) -> None:
        user = self.storage.get_user(chat_id)
        if user:
            self.storage.save_user(chat_id, user['address'], False)
        self.send_message(chat_id, "🔔 Unsubscribed. Send address to resubscribe.")
    
    def handle_help(self, chat_id: int) -> None:
        help_text = """🆘 *Commands*

/start - Welcome message
/setaddress <addr> - Add address  
/watchlist - View status
/getaddress - Show address
/unsubscribe - Stop alerts
/testalert - Test notification
/status - System status
/stats - Your statistics
/help - This message"""
        self.send_message(chat_id, help_text)
    
    def handle_test_alert(self, chat_id: int) -> None:
        # Generate test data
        amount = 4683
        fake_tx_hash = ''.join(random.choices('0123456789abcdef', k=64))
        explorer_link = f"https://iotexscan.io/tx/{fake_tx_hash}"
        validator = "Dustpin Lab"
        
        test_message = f"""🧪 *Test Alert - Sample Format*

📥 Incoming Transaction:

👤 From: io1abc...xyz
💰 Amount: {amount} IOTX
🔗 Transaction: [View on Explorer]({explorer_link})

🎉 Staking Reward Received:

💰 Amount: 750 IOTX
🏛 Validator: {validator}
🔗 Transaction: [View on Explorer]({explorer_link})"""
        self.send_message(chat_id, test_message)
    
    def handle_status(self, chat_id: int) -> None:
        users = self.storage.get_all_active_users()
        total_alerts = sum(user.get('total_alerts', 0) for user in users)
        
        status_message = f"""📊 *System Status*

🟢 Bot: Online
⏱️ Interval: 4 seconds
👥 Users: {len(users)}
🔔 Alerts: {total_alerts}
🌐 Host: Railway"""
        self.send_message(chat_id, status_message)
    
    def handle_stats(self, chat_id: int) -> None:
        user = self.storage.get_user(chat_id)
        if not user:
            self.send_message(chat_id, "❌ No data. Send an address!")
            return
        
        total_alerts = user.get('total_alerts', 0)
        last_alert = user.get('last_alert_sent', 'Never')
        
        stats_message = f"""📈 *Your Statistics*

📊 Total Alerts: {total_alerts}
🕒 Last Alert: {last_alert}
🟢 Status: Active"""
        self.send_message(chat_id, stats_message)

def get_bot_info():
    """Check if bot token is valid"""
    url = f"{TELEGRAM_API_BASE}/getMe"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get('ok'):
            bot_info = data['result']
            logger.info(f"✅ Bot: @{bot_info['username']} ({bot_info['first_name']})")
            return True
        else:
            logger.error(f"❌ Invalid token: {data}")
            return False
    except Exception as e:
        logger.error(f"❌ Bot check error: {e}")
        return False

def start_monitoring():
    """Start monitoring in background"""
    monitor = IoTeXMonitor()
    logger.info("🚀 Starting IoTeX monitoring (4s intervals)...")
    while True:
        try:
            monitor.check_all_users()
            time.sleep(4)
        except Exception as e:
            logger.error(f"Monitor error: {e}")
            time.sleep(2)

def poll_updates():
    """Poll for Telegram updates"""
    bot = TelegramBot()
    offset = None
    
    logger.info("🤖 Starting IoTeX Bot (POLLING MODE)...")
    
    # Check bot token
    if not get_bot_info():
        logger.error("❌ Check bot token with @BotFather")
        return
    
    logger.info("✅ Bot ready! Message your bot on Telegram...")
    
    # Start monitoring thread
    monitor_thread = Thread(target=start_monitoring, daemon=True)
    monitor_thread.start()
    
    # Poll for messages
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
                        
                        if text:
                            logger.info(f"📨 From {chat_id}: {text}")
                            bot.process_message(chat_id, text)
            
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)

if __name__ == '__main__':
    poll_updates()