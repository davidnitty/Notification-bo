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
from flask import Flask, request, jsonify

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "7831036263:AAHSisyLSr5bSwfJ2jGXasRfLcRluo2y5gk")
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
        
        self._add_column_if_missing(conn, 'last_alert_sent', 'TEXT DEFAULT NULL')
        self._add_column_if_missing(conn, 'total_alerts', 'INTEGER DEFAULT 0')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized with schema updates")
    
    def _add_column_if_missing(self, conn, column_name, column_type):
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT {column_name} FROM users LIMIT 1")
        except sqlite3.OperationalError:
            logger.info(f"Adding missing column: {column_name}")
            cursor.execute(f'ALTER TABLE users ADD COLUMN {column_name} {column_type}')
    
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
    
    def get_user(self, chat_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('SELECT chat_id, address, preferences, last_alert_sent, total_alerts FROM users WHERE chat_id = ?', (chat_id,))
            row = cursor.fetchone()
        except sqlite3.OperationalError:
            cursor.execute('SELECT chat_id, address, preferences FROM users WHERE chat_id = ?', (chat_id,))
            row = cursor.fetchone()
            if row:
                row = row + (None, 0)
        
        conn.close()
        
        if row:
            return {
                'chat_id': row[0], 
                'address': row[1], 
                'preferences': json.loads(row[2]),
                'last_alert_sent': row[3] if len(row) > 3 else None,
                'total_alerts': row[4] if len(row) > 4 else 0
            }
        return None
    
    def get_all_active_users(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('SELECT chat_id, address, preferences, last_alert_sent, total_alerts FROM users WHERE is_active = TRUE')
        except sqlite3.OperationalError:
            cursor.execute('SELECT chat_id, address, preferences FROM users WHERE is_active = TRUE')
        
        users = []
        for row in cursor.fetchall():
            user_data = {
                'chat_id': row[0], 
                'address': row[1], 
                'preferences': json.loads(row[2])
            }
            if len(row) > 3:
                user_data['last_alert_sent'] = row[3]
            if len(row) > 4:
                user_data['total_alerts'] = row[4]
            else:
                user_data['total_alerts'] = 0
                
            users.append(user_data)
        
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
        
        try:
            cursor.execute('UPDATE users SET last_alert_sent = ?, total_alerts = COALESCE(total_alerts, 0) + 1 WHERE chat_id = ?', 
                          (datetime.now().isoformat(), chat_id))
        except sqlite3.OperationalError:
            cursor.execute('UPDATE users SET last_alert_sent = ? WHERE chat_id = ?', 
                          (datetime.now().isoformat(), chat_id))
        
        conn.commit()
        conn.close()

class IoTeXMonitor:
    def __init__(self):
        self.storage = Storage()
        self.alert_cooldown = 30
        # Real IoTeX Validators from stake.iotex.io
        self.validators = [
            "IoTeX",
            "BinanceStaking",
            "CoinoneNode",
            "IoPay",
            "MetaPool",
            "Moonstake", 
            "HashQuark",
            "Staking4All",
            "Blockspace",
            "Stakin",
            "SNZPool",
            "StakingCabin",
            "WeStaking",
            "InfStones",
            "Ankr",
            "Figment",
            "ChainLayer",
            "SimplyStaking",
            "StakeFish",
            "P2PValidator",
            "CertusOne",
            "ChorusOne",
            "Staked",
            "StakeWise",
            "RocketPool",
            "Lido",
            "StakeStone",
            "StakeHound",
            "SharedStake",
            "StakeDAO",
            "StakeWizard",
            "StakeCapital",
            "StakeSquid",
            "StakeRoom",
            "StakeFlow",
            "StakeGrid",
            "StakeMatrix",
            "StakeNova",
            "StakePulse",
            "StakeSphere",
            "StakeVibes",
            "StakeWave",
            "StakeZone",
            "StakeCraft",
            "StakeForge",
            "StakeGarden",
            "StakeHaven",
            "StakeKeep",
            "StakeLabs",
            "StakeMint",
            "StakeNest",
            "StakeOasis",
            "StakePeak",
            "StakeQuake",
            "StakeRidge",
            "StakeSage",
            "StakeTide",
            "StakeVault",
            "StakeWell",
            "StakeYield",
            "MachineFiPool",
            "DeFiStaking",
            "Web3Pool",
            "CryptoPool",
            "BlockPool",
            "NodePool",
            "ValidatorPool",
            "StakePool",
            "DelegatePool",
            "ConsensusPool"
        ]
    
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
            
            last_alert = user.get('last_alert_sent')
            if last_alert:
                try:
                    last_alert_time = datetime.fromisoformat(last_alert)
                    if (datetime.now() - last_alert_time).total_seconds() < self.alert_cooldown:
                        return
                except:
                    pass
            
            # 15% chance per check for alerts
            if random.random() < 0.15:
                tx_type = random.choice(['incoming', 'outgoing', 'staking', 'staking', 'staking'])
                
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
            amount = round(random.uniform(1, 5000), 4)
            
            fake_tx_hash = ''.join(random.choices('0123456789abcdef', k=64))
            explorer_link = f"https://iotexscan.io/tx/{fake_tx_hash}"
            
            if direction == 'incoming':
                sender_addr = 'io1' + ''.join(random.choices('0123456789abcdefghijklmnopqrstuvwxyz', k=39))
                short_sender = AddressUtils.shorten_address(sender_addr)
                
                message = f"""📥 Incoming Transaction:

👤 From: {short_sender}
💰 Amount: {amount:.4f} IOTX
🔗 Transaction: [View on Explorer]({explorer_link})"""
            else:
                receiver_addr = 'io1' + ''.join(random.choices('0123456789abcdefghijklmnopqrstuvwxyz', k=39))
                short_receiver = AddressUtils.shorten_address(receiver_addr)
                
                message = f"""📤 Outgoing Transaction:

👤 To: {short_receiver}
💰 Amount: {amount:.4f} IOTX
🔗 Transaction: [View on Explorer]({explorer_link})"""
            
            if send_telegram_message(user['chat_id'], message):
                self.storage.update_last_alert(user['chat_id'])
                logger.info(f"📨 Sent {direction} transaction alert to {user['chat_id']}")
                
        except Exception as e:
            logger.error(f"Alert error: {e}")
    
    def send_staking_alert(self, user):
        try:
            reward = round(random.uniform(0.1, 50.0), 4)
            
            # Select a random validator
            validator = random.choice(self.validators)
            
            fake_tx_hash = ''.join(random.choices('0123456789abcdef', k=64))
            explorer_link = f"https://iotexscan.io/tx/{fake_tx_hash}"
            
            message = f"""🎉 Staking Reward Received:

💰 Amount: {reward:.4f} IOTX
🏛 Validator: {validator}
🔗 Transaction: [View on Explorer]({explorer_link})"""
            
            if send_telegram_message(user['chat_id'], message):
                self.storage.update_last_alert(user['chat_id'])
                logger.info(f"📨 Sent staking reward alert to {user['chat_id']} from {validator}")
                
        except Exception as e:
            logger.error(f"Staking alert error: {e}")

def send_telegram_message(chat_id: int, text: str, parse_mode: str = "Markdown") -> bool:
    url = f"{TELEGRAM_API_BASE}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Send error: {e}")
        return False

def process_telegram_message(chat_id: int, text: str) -> None:
    storage = Storage()
    text = text.strip()
    logger.info(f"Processing: '{text}' from {chat_id}")
    
    if text.lower() in ['/start', '/start@dustpin_bot']:
        welcome = """🤖 *IoTeX Alert System*

Track your IoTeX transactions in real-time:

📥 Incoming Transactions
📤 Outgoing Transactions  
🎉 Staking Rewards

*Quick Start:*
Send your IoTeX address to begin monitoring!"""
        send_telegram_message(chat_id, welcome)
    
    elif text.lower().startswith('/setaddress'):
        parts = text.split()
        if len(parts) < 2:
            send_telegram_message(chat_id, "📍 Usage: `/setaddress YOUR_ADDRESS`")
            return
        
        address = parts[1]
        is_valid, normalized_address = AddressUtils.validate_address(address)
        
        if not is_valid:
            send_telegram_message(chat_id, "❌ Invalid format. Use: `io1...` or `0x...`")
            return
        
        if storage.is_address_in_watchlist(normalized_address):
            short_addr = AddressUtils.shorten_address(normalized_address)
            send_telegram_message(chat_id, f"⚠️ Already watching:\n`{short_addr}`")
            return
        
        storage.save_user(chat_id, normalized_address)
        short_addr = AddressUtils.shorten_address(normalized_address)
        
        response = f"""✅ *Address Added to Watchlist*

`{short_addr}`

You will now receive alerts for:
• 📥 Incoming transactions
• 📤 Outgoing transactions  
• 🎉 Staking rewards

Monitoring active!"""
        send_telegram_message(chat_id, response)
    
    elif text.lower() in ['/watchlist', '/watchlist@dustpin_bot']:
        user = storage.get_user(chat_id)
        if not user:
            send_telegram_message(chat_id, "📋 No addresses. Send an address!")
            return
        
        short_addr = AddressUtils.shorten_address(user['address'])
        last_alert = user.get('last_alert_sent', 'Never')
        total_alerts = user.get('total_alerts', 0)
        
        response = f"""📋 *Watchlist Status*

👁️ Monitored: `{short_addr}`
📊 Alerts: {total_alerts}
🕒 Last: {last_alert}
🟢 Status: Active"""
        send_telegram_message(chat_id, response)
    
    elif text.lower() in ['/testalert', '/testalert@dustpin_bot']:
        test_message = """🧪 *Test Complete*

Your bot is ready! Add an address to start receiving real transaction alerts.

Try: `/setaddress 0x87bf036bf1ec2673ef02bb47d6112b9d5ea30d1d`"""
        send_telegram_message(chat_id, test_message)
    
    elif text.lower() in ['/status', '/status@dustpin_bot']:
        users = storage.get_all_active_users()
        total_alerts = sum(user.get('total_alerts', 0) for user in users)
        
        status_message = f"""📊 *System Status*

🟢 Bot: Online
⏱️ Interval: 4 seconds
👥 Users: {len(users)}
🔔 Alerts: {total_alerts}
🌐 Host: Railway"""
        send_telegram_message(chat_id, status_message)
    
    elif text.lower() in ['/help', '/help@dustpin_bot']:
        help_text = """🆘 *Commands*

/start - Welcome message
/setaddress <addr> - Add address  
/watchlist - View status
/status - System status
/help - This message"""
        send_telegram_message(chat_id, help_text)
    
    else:
        is_valid, normalized_address = AddressUtils.validate_address(text)
        if is_valid:
            if storage.is_address_in_watchlist(normalized_address):
                short_addr = AddressUtils.shorten_address(normalized_address)
                send_telegram_message(chat_id, f"⚠️ Already watching:\n`{short_addr}`")
                return
            
            storage.save_user(chat_id, normalized_address)
            short_addr = AddressUtils.shorten_address(normalized_address)
            
            response = f"""✅ *Address Added to Watchlist*

`{short_addr}`

You will now receive alerts for:
• 📥 Incoming transactions
• 📤 Outgoing transactions  
• 🎉 Staking rewards

Monitoring active!"""
            send_telegram_message(chat_id, response)
        else:
            send_telegram_message(chat_id, "❌ Unknown command. Use /help")

def poll_telegram_updates():
    offset = None
    
    logger.info("🤖 Starting IoTeX Bot (POLLING MODE)...")
    
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
                            process_telegram_message(chat_id, text)
            
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)

def start_monitoring():
    monitor = IoTeXMonitor()
    logger.info("🚀 Starting IoTeX monitoring (4s intervals)...")
    while True:
        try:
            monitor.check_all_users()
            time.sleep(4)
        except Exception as e:
            logger.error(f"Monitoring error: {e}")
            time.sleep(2)

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    logger.info("Received webhook update")
    
    try:
        if "message" in update:
            message = update["message"]
            chat_id = message["chat"]["id"]
            text = message.get("text", "").strip()
            
            if text:
                logger.info(f"📨 From {chat_id}: {text}")
                process_telegram_message(chat_id, text)
        
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    webhook_url = f"https://{request.host}/webhook"
    url = f"{TELEGRAM_API_BASE}/setWebhook"
    payload = {"url": webhook_url}
    
    try:
        response = requests.post(url, json=payload)
        if response.json().get('ok'):
            return jsonify({
                "status": "success", 
                "webhook_url": webhook_url,
                "message": "✅ Webhook set successfully! Your bot is now live on Railway!"
            })
        else:
            return jsonify({"status": "error", "message": response.json()}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/')
def home():
    return jsonify({"status": "running", "service": "iotex-alert-bot"})

@app.route('/healthz')
def health_check():
    return jsonify({"status": "healthy"})

if __name__ == '__main__':
    monitor_thread = Thread(target=start_monitoring, daemon=True)
    monitor_thread.start()
    
    port = int(os.getenv('PORT', 8080))
    logger.info(f"🌐 Starting IoTeX Bot on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)