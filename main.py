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
IOTEX_API_URL = "https://api.iotex.me/api/graphql"

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
        self._add_column_if_missing(conn, 'last_checked_block', 'INTEGER DEFAULT 0')
        
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
            (chat_id, address, preferences, created_at, is_active, last_alert_sent, total_alerts, last_checked_block)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (chat_id, address, preferences, datetime.now().isoformat(), is_active, None, 0, 0))
        conn.commit()
        conn.close()
    
    def get_user(self, chat_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('SELECT chat_id, address, preferences, last_alert_sent, total_alerts, last_checked_block FROM users WHERE chat_id = ?', (chat_id,))
            row = cursor.fetchone()
        except sqlite3.OperationalError:
            cursor.execute('SELECT chat_id, address, preferences FROM users WHERE chat_id = ?', (chat_id,))
            row = cursor.fetchone()
            if row:
                row = row + (None, 0, 0)
        
        conn.close()
        
        if row:
            return {
                'chat_id': row[0], 
                'address': row[1], 
                'preferences': json.loads(row[2]),
                'last_alert_sent': row[3] if len(row) > 3 else None,
                'total_alerts': row[4] if len(row) > 4 else 0,
                'last_checked_block': row[5] if len(row) > 5 else 0
            }
        return None
    
    def get_all_active_users(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('SELECT chat_id, address, preferences, last_alert_sent, total_alerts, last_checked_block FROM users WHERE is_active = TRUE')
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
            if len(row) > 5:
                user_data['last_checked_block'] = row[5]
            else:
                user_data['last_checked_block'] = 0
                
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
    
    def update_last_checked_block(self, chat_id: int, block_number: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('UPDATE users SET last_checked_block = ? WHERE chat_id = ?', 
                          (block_number, chat_id))
        except sqlite3.OperationalError:
            pass  # Column might not exist yet
        
        conn.commit()
        conn.close()

class IoTeXAPI:
    def __init__(self):
        self.api_url = IOTEX_API_URL
    
    def get_latest_block(self):
        """Get the latest block number from IoTeX"""
        query = """
        {
            chain {
                mostRecentBlock {
                    height
                }
            }
        }
        """
        try:
            response = requests.post(self.api_url, json={'query': query}, timeout=10)
            data = response.json()
            return int(data['data']['chain']['mostRecentBlock']['height'])
        except Exception as e:
            logger.error(f"Error getting latest block: {e}")
            return None
    
    def get_transactions_for_address(self, address: str, from_block: int, to_block: int):
        """Get real transactions for an address from IoTeX blockchain"""
        query = """
        query GetTransactions($address: String!, $fromBlock: Int!, $toBlock: Int!) {
            transactionsByAddress(address: $address, fromBlock: $fromBlock, toBlock: $toBlock) {
                transactions {
                    hash
                    from
                    to
                    value
                    block {
                        height
                        timestamp {
                            seconds
                        }
                    }
                }
            }
        }
        """
        
        variables = {
            "address": address,
            "fromBlock": from_block,
            "toBlock": to_block
        }
        
        try:
            response = requests.post(self.api_url, json={'query': query, 'variables': variables}, timeout=10)
            data = response.json()
            
            if 'errors' in data:
                logger.error(f"GraphQL errors: {data['errors']}")
                return []
            
            transactions = data.get('data', {}).get('transactionsByAddress', {}).get('transactions', [])
            return self._normalize_transactions(transactions, address)
        except Exception as e:
            logger.error(f"Error getting transactions: {e}")
            return []
    
    def _normalize_transactions(self, transactions, user_address):
        """Normalize transaction data"""
        normalized = []
        
        for tx in transactions:
            # Convert value from wei to IOTX (18 decimals)
            value_wei = int(tx.get('value', '0'))
            value_iotx = value_wei / 10**18
            
            # Determine direction
            direction = "IN" if tx.get('to', '').lower() == user_address.lower() else "OUT"
            
            # Convert timestamp
            timestamp_seconds = tx.get('block', {}).get('timestamp', {}).get('seconds', 0)
            if timestamp_seconds:
                timestamp = datetime.fromtimestamp(timestamp_seconds).isoformat()
            else:
                timestamp = datetime.now().isoformat()
            
            normalized_tx = {
                'hash': tx.get('hash'),
                'from': tx.get('from'),
                'to': tx.get('to'),
                'amount': value_iotx,
                'token': 'IOTX',
                'direction': direction,
                'block_number': tx.get('block', {}).get('height'),
                'timestamp': timestamp
            }
            normalized.append(normalized_tx)
        
        return normalized

class IoTeXMonitor:
    def __init__(self):
        self.storage = Storage()
        self.iotex_api = IoTeXAPI()
        self.alert_cooldown = 30
    
    def check_all_users(self):
        try:
            users = self.storage.get_all_active_users()
            if not users:
                return
            
            # Get current block height
            current_block = self.iotex_api.get_latest_block()
            if not current_block:
                logger.error("Could not get current block height")
                return
            
            if int(time.time()) % 60 < 4:
                total_alerts = sum(user.get('total_alerts', 0) for user in users)
                logger.info(f"👁️ Monitoring {len(users)} users | Block: {current_block} | Alerts: {total_alerts}")
            
            for user in users:
                self.check_user_transactions(user, current_block)
                
        except Exception as e:
            logger.error(f"Monitor error: {e}")
    
    def check_user_transactions(self, user, current_block):
        try:
            if not user.get('address'):
                return
            
            user_address = user['address']
            last_checked_block = user.get('last_checked_block', 0)
            
            # Start from last checked block + 1, or current block - 1000 for new users
            from_block = last_checked_block + 1 if last_checked_block > 0 else max(1, current_block - 1000)
            to_block = current_block
            
            if from_block > to_block:
                return  # No new blocks to check
            
            logger.info(f"Checking blocks {from_block}-{to_block} for {AddressUtils.shorten_address(user_address)}")
            
            # Get real transactions from IoTeX blockchain
            transactions = self.iotex_api.get_transactions_for_address(user_address, from_block, to_block)
            
            if transactions:
                logger.info(f"Found {len(transactions)} transactions for {AddressUtils.shorten_address(user_address)}")
                
                for tx in transactions:
                    if tx['direction'] == 'IN' and user['preferences'].get('tx_in', True):
                        self.send_transaction_alert(user, tx, 'incoming')
                    elif tx['direction'] == 'OUT' and user['preferences'].get('tx_out', True):
                        self.send_transaction_alert(user, tx, 'outgoing')
            
            # Update last checked block
            self.storage.update_last_checked_block(user['chat_id'], to_block)
                    
        except Exception as e:
            logger.error(f"Transaction check error: {e}")
    
    def send_transaction_alert(self, user, tx, direction):
        try:
            amount = tx['amount']
            explorer_link = f"https://iotexscan.io/tx/{tx['hash']}"
            
            if direction == 'incoming':
                short_sender = AddressUtils.shorten_address(tx['from'])
                
                message = f"""📥 Incoming Transaction:

👤 From: {short_sender}
💰 Amount: {amount:.4f} IOTX
🔗 Transaction: [View on Explorer]({explorer_link})"""
            else:
                short_receiver = AddressUtils.shorten_address(tx['to'])
                
                message = f"""📤 Outgoing Transaction:

👤 To: {short_receiver}
💰 Amount: {amount:.4f} IOTX
🔗 Transaction: [View on Explorer]({explorer_link})"""
            
            if send_telegram_message(user['chat_id'], message):
                self.storage.update_last_alert(user['chat_id'])
                logger.info(f"📨 Sent REAL {direction} transaction alert to {user['chat_id']}")
                
        except Exception as e:
            logger.error(f"Alert error: {e}")

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
        welcome = """🤖 *IoTeX Alert System - REAL Monitoring*

Now monitoring REAL IoTeX blockchain transactions!

📥 Incoming Transactions
📤 Outgoing Transactions  

*Quick Start:*
Send your IoTeX address to begin REAL monitoring!"""
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

🎯 *REAL IoTeX Monitoring Activated*

I'm now scanning the actual IoTeX blockchain for your transactions.

You will receive alerts for:
• 📥 Incoming transactions (REAL)
• 📤 Outgoing transactions (REAL)

Monitoring starts from the current block!"""
        send_telegram_message(chat_id, response)
    
    elif text.lower() in ['/watchlist', '/watchlist@dustpin_bot']:
        user = storage.get_user(chat_id)
        if not user:
            send_telegram_message(chat_id, "📋 No addresses. Send an address!")
            return
        
        short_addr = AddressUtils.shorten_address(user['address'])
        last_alert = user.get('last_alert_sent', 'Never')
        total_alerts = user.get('total_alerts', 0)
        last_block = user.get('last_checked_block', 0)
        
        response = f"""📋 *Watchlist Status*

👁️ Monitored: `{short_addr}`
📊 Alerts: {total_alerts}
🕒 Last: {last_alert}
📦 Last Block: {last_block}
🟢 Status: REAL Monitoring"""
        send_telegram_message(chat_id, response)
    
    elif text.lower() in ['/testalert', '/testalert@dustpin_bot']:
        test_message = """🧪 *Test Complete - REAL Monitoring Active*

Your bot is now monitoring the REAL IoTeX blockchain!

Add an address to start receiving REAL transaction alerts.

Try: `/setaddress 0x87bf036bf1ec2673ef02bb47d6112b9d5ea30d1d`"""
        send_telegram_message(chat_id, test_message)
    
    elif text.lower() in ['/status', '/status@dustpin_bot']:
        users = storage.get_all_active_users()
        total_alerts = sum(user.get('total_alerts', 0) for user in users)
        
        # Get current block
        iotex_api = IoTeXAPI()
        current_block = iotex_api.get_latest_block() or "Unknown"
        
        status_message = f"""📊 *System Status*

🟢 Bot: Online (REAL Monitoring)
⏱️ Interval: 4 seconds
👥 Users: {len(users)}
🔔 Alerts: {total_alerts}
📦 Current Block: {current_block}
🌐 Host: Railway"""
        send_telegram_message(chat_id, status_message)
    
    elif text.lower() in ['/help', '/help@dustpin_bot']:
        help_text = """🆘 *Commands*

/start - Welcome message
/setaddress <addr> - Add address for REAL monitoring
/watchlist - View status
/status - System status
/help - This message

🎯 *Now monitoring REAL IoTeX blockchain transactions!*"""
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

🎯 *REAL IoTeX Monitoring Activated*

I'm now scanning the actual IoTeX blockchain for your transactions.

You will receive alerts for REAL:
• 📥 Incoming transactions
• 📤 Outgoing transactions

Monitoring starts from the current block!"""
            send_telegram_message(chat_id, response)
        else:
            send_telegram_message(chat_id, "❌ Unknown command. Use /help")

def poll_telegram_updates():
    offset = None
    
    logger.info("🤖 Starting IoTeX Bot (REAL Monitoring)...")
    
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
    logger.info("🚀 Starting REAL IoTeX blockchain monitoring (4s intervals)...")
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
    return jsonify({"status": "running", "service": "iotex-alert-bot", "monitoring": "REAL"})

@app.route('/healthz')
def health_check():
    return jsonify({"status": "healthy"})

if __name__ == '__main__':
    monitor_thread = Thread(target=start_monitoring, daemon=True)
    monitor_thread.start()
    
    port = int(os.getenv('PORT', 8080))
    logger.info(f"🌐 Starting IoTeX Bot on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)