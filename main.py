import os
import json
import time
import logging
import requests
import hashlib
from datetime import datetime
from typing import Optional, Dict, List
import sqlite3
from threading import Thread
import pytz

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '7831036263:AAHSisyLSr5bSwfJ2jGXasRfLcRluo2y5gk')
IOTEX_RPC_URL = os.getenv('IOTEX_RPC_URL', 'https://babel-api.mainnet.iotex.io')
IOTEX_GRAPHQL_URL = os.getenv('IOTEX_GRAPHQL_URL', 'https://analyser-api.iotex.io/graphql')
CONFIRMATIONS = int(os.getenv('CONFIRMATIONS', '3'))
POLL_INTERVAL_SEC = int(os.getenv('POLL_INTERVAL_SEC', '20'))
TIMEZONE = pytz.timezone(os.getenv('TZ', 'Africa/Lagos'))
DB_PATH = os.getenv('DB_PATH', 'iotex_bot.db')

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Users table
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                io_address TEXT,
                eth_address TEXT,
                alert_rewards INTEGER DEFAULT 1,
                alert_tx_in INTEGER DEFAULT 1,
                alert_tx_out INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Processed transactions table (for deduplication)
        c.execute('''
            CREATE TABLE IF NOT EXISTS processed_txs (
                chat_id INTEGER,
                tx_hash TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, tx_hash)
            )
        ''')
        
        # Last seen blocks
        c.execute('''
            CREATE TABLE IF NOT EXISTS last_blocks (
                chat_id INTEGER PRIMARY KEY,
                block_number INTEGER
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def get_connection(self):
        return sqlite3.connect(self.db_path)
    
    def save_user(self, chat_id: int, io_address: str, eth_address: str):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO users (chat_id, io_address, eth_address)
            VALUES (?, ?, ?)
        ''', (chat_id, io_address, eth_address))
        conn.commit()
        conn.close()
    
    def get_user(self, chat_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE chat_id = ?', (chat_id,))
        row = c.fetchone()
        conn.close()
        
        if row:
            return {
                'chat_id': row[0],
                'io_address': row[1],
                'eth_address': row[2],
                'alert_rewards': row[3],
                'alert_tx_in': row[4],
                'alert_tx_out': row[5]
            }
        return None
    
    def get_all_users(self) -> List[Dict]:
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE io_address IS NOT NULL')
        rows = c.fetchall()
        conn.close()
        
        return [{
            'chat_id': row[0],
            'io_address': row[1],
            'eth_address': row[2],
            'alert_rewards': row[3],
            'alert_tx_in': row[4],
            'alert_tx_out': row[5]
        } for row in rows]
    
    def delete_user(self, chat_id: int):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('DELETE FROM users WHERE chat_id = ?', (chat_id,))
        c.execute('DELETE FROM processed_txs WHERE chat_id = ?', (chat_id,))
        c.execute('DELETE FROM last_blocks WHERE chat_id = ?', (chat_id,))
        conn.commit()
        conn.close()
    
    def update_settings(self, chat_id: int, rewards: int, tx_in: int, tx_out: int):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''
            UPDATE users 
            SET alert_rewards = ?, alert_tx_in = ?, alert_tx_out = ?
            WHERE chat_id = ?
        ''', (rewards, tx_in, tx_out, chat_id))
        conn.commit()
        conn.close()
    
    def is_tx_processed(self, chat_id: int, tx_hash: str) -> bool:
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT 1 FROM processed_txs WHERE chat_id = ? AND tx_hash = ?',
                  (chat_id, tx_hash))
        exists = c.fetchone() is not None
        conn.close()
        return exists
    
    def mark_tx_processed(self, chat_id: int, tx_hash: str):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO processed_txs (chat_id, tx_hash) VALUES (?, ?)',
                  (chat_id, tx_hash))
        conn.commit()
        conn.close()
    
    def get_last_block(self, chat_id: int) -> Optional[int]:
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT block_number FROM last_blocks WHERE chat_id = ?', (chat_id,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else None
    
    def update_last_block(self, chat_id: int, block_number: int):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO last_blocks (chat_id, block_number) VALUES (?, ?)',
                  (chat_id, block_number))
        conn.commit()
        conn.close()

class AddressConverter:
    @staticmethod
    def io_to_eth(io_address: str) -> Optional[str]:
        """Convert io address to 0x format"""
        if not io_address.startswith('io'):
            return None
        
        try:
            import bech32
            _, data = bech32.bech32_decode(io_address)
            if data is None:
                return None
            decoded = bech32.convertbits(data, 5, 8, False)
            if decoded is None or len(decoded) != 20:
                return None
            return '0x' + ''.join(f'{b:02x}' for b in decoded)
        except:
            # Fallback: simple conversion
            return None
    
    @staticmethod
    def eth_to_io(eth_address: str) -> Optional[str]:
        """Convert 0x address to io format"""
        if not eth_address.startswith('0x'):
            return None
        
        try:
            import bech32
            addr_bytes = bytes.fromhex(eth_address[2:])
            if len(addr_bytes) != 20:
                return None
            data = bech32.convertbits(addr_bytes, 8, 5)
            if data is None:
                return None
            return bech32.bech32_encode('io', data)
        except:
            return None
    
    @staticmethod
    def validate_address(address: str) -> bool:
        """Validate IoTeX address format"""
        if address.startswith('io'):
            return len(address) == 41 or len(address) == 42
        elif address.startswith('0x'):
            return len(address) == 42 and all(c in '0123456789abcdefABCDEF' for c in address[2:])
        return False
    
    @staticmethod
    def normalize_address(address: str) -> tuple:
        """Returns (io_address, eth_address)"""
        if address.startswith('io'):
            eth = AddressConverter.io_to_eth(address)
            return (address.lower(), eth.lower() if eth else None)
        elif address.startswith('0x'):
            io = AddressConverter.eth_to_io(address)
            return (io.lower() if io else None, address.lower())
        return (None, None)

class IoTeXAPI:
    def __init__(self, rpc_url: str, graphql_url: str):
        self.rpc_url = rpc_url
        self.graphql_url = graphql_url
    
    def get_current_block(self) -> Optional[int]:
        """Get current block height"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_blockNumber",
                "params": [],
                "id": 1
            }
            response = requests.post(self.rpc_url, json=payload, timeout=10)
            if response.status_code == 200:
                result = response.json().get('result')
                return int(result, 16) if result else None
        except Exception as e:
            logger.error(f"Error getting current block: {e}")
        return None
    
    def get_transactions(self, address: str, start_block: int, end_block: int) -> List[Dict]:
        """Get transactions for an address using GraphQL"""
        transactions = []
        
        try:
            query = """
            query {
              account(address: "%s") {
                address
                transactions(first: 50) {
                  hash
                  from
                  to
                  amount
                  gasPrice
                  gasUsed
                  timestamp
                  blockNumber
                  status
                }
              }
            }
            """ % address
            
            response = requests.post(
                self.graphql_url,
                json={'query': query},
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data and data['data'].get('account'):
                    txs = data['data']['account'].get('transactions', [])
                    for tx in txs:
                        if tx.get('blockNumber'):
                            block_num = int(tx['blockNumber'])
                            if start_block <= block_num <= end_block:
                                transactions.append(tx)
        except Exception as e:
            logger.error(f"Error fetching transactions: {e}")
        
        return transactions
    
    def get_staking_info(self, address: str) -> Optional[Dict]:
        """Get staking information for address"""
        try:
            query = """
            query {
              account(address: "%s") {
                address
                stakingBuckets {
                  amount
                  stakedDuration
                  delegate {
                    name
                    address
                  }
                  unstakeStartEpoch
                }
              }
            }
            """ % address
            
            response = requests.post(
                self.graphql_url,
                json={'query': query},
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data and data['data'].get('account'):
                    return data['data']['account']
        except Exception as e:
            logger.error(f"Error fetching staking info: {e}")
        
        return None

class TelegramBot:
    def __init__(self, db: Database, iotex_api: IoTeXAPI):
        self.db = db
        self.iotex_api = iotex_api
        self.offset = 0
    
    def send_message(self, chat_id: int, text: str, parse_mode: str = 'HTML'):
        """Send message to user"""
        try:
            payload = {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': parse_mode,
                'disable_web_page_preview': True
            }
            response = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False
    
    def get_updates(self) -> List[Dict]:
        """Get updates from Telegram"""
        try:
            response = requests.get(
                f"{TELEGRAM_API}/getUpdates",
                params={'offset': self.offset, 'timeout': 30}
            )
            if response.status_code == 200:
                return response.json().get('result', [])
        except Exception as e:
            logger.error(f"Error getting updates: {e}")
        return []
    
    def handle_start(self, chat_id: int):
        """Handle /start command"""
        text = """
👋 <b>Welcome to IoTeX Alert Bot!</b>

I'll notify you instantly when:
• 🎉 You receive staking rewards
• 📥 You receive IOTX transactions
• 📤 You send IOTX transactions

<b>Getting Started:</b>
Use /setaddress followed by your IoTeX address

<b>Example:</b>
<code>/setaddress io1abc123...</code>
or
<code>/setaddress 0xabc123...</code>

<b>Available Commands:</b>
/setaddress - Set your IoTeX address
/getaddress - View your saved address
/settings - Customize alert preferences
/unsubscribe - Stop all alerts
/help - Show this message

Let's get started! 🚀
"""
        self.send_message(chat_id, text)
    
    def handle_setaddress(self, chat_id: int, address: str):
        """Handle /setaddress command"""
        if not address:
            self.send_message(
                chat_id,
                "❌ Please provide an address.\n\n"
                "Usage: <code>/setaddress io1abc123...</code>"
            )
            return
        
        if not AddressConverter.validate_address(address):
            self.send_message(
                chat_id,
                "❌ Invalid address format.\n\n"
                "Please provide a valid IoTeX address:\n"
                "• Native format: <code>io1...</code> (41-42 characters)\n"
                "• EVM format: <code>0x...</code> (42 characters)"
            )
            return
        
        io_addr, eth_addr = AddressConverter.normalize_address(address)
        
        if not io_addr and not eth_addr:
            self.send_message(chat_id, "❌ Failed to process address. Please try again.")
            return
        
        self.db.save_user(chat_id, io_addr, eth_addr)
        
        # Initialize last block to current
        current_block = self.iotex_api.get_current_block()
        if current_block:
            self.db.update_last_block(chat_id, current_block)
        
        display_addr = io_addr if io_addr else eth_addr
        text = f"""
✅ <b>Address saved successfully!</b>

📍 <b>Your address:</b>
<code>{display_addr}</code>

I'll now monitor this address and send you alerts for:
• Staking rewards 🎉
• Incoming transactions 📥
• Outgoing transactions 📤

Use /settings to customize which alerts you receive.
"""
        self.send_message(chat_id, text)
        logger.info(f"User {chat_id} set address: {display_addr}")
    
    def handle_getaddress(self, chat_id: int):
        """Handle /getaddress command"""
        user = self.db.get_user(chat_id)
        
        if not user or not user.get('io_address'):
            self.send_message(
                chat_id,
                "❌ No address saved.\n\n"
                "Use /setaddress to set your IoTeX address."
            )
            return
        
        io_addr = user['io_address']
        eth_addr = user['eth_address']
        
        text = f"""
📍 <b>Your saved address:</b>

<b>Native format:</b>
<code>{io_addr if io_addr else 'N/A'}</code>

<b>EVM format:</b>
<code>{eth_addr if eth_addr else 'N/A'}</code>

<b>Alert Settings:</b>
• Staking Rewards: {'✅ ON' if user['alert_rewards'] else '❌ OFF'}
• Incoming TX: {'✅ ON' if user['alert_tx_in'] else '❌ OFF'}
• Outgoing TX: {'✅ ON' if user['alert_tx_out'] else '❌ OFF'}

Use /settings to change your preferences.
"""
        self.send_message(chat_id, text)
    
    def handle_settings(self, chat_id: int, args: str = ''):
        """Handle /settings command"""
        user = self.db.get_user(chat_id)
        
        if not user or not user.get('io_address'):
            self.send_message(
                chat_id,
                "❌ Please set your address first using /setaddress"
            )
            return
        
        if not args:
            text = f"""
⚙️ <b>Alert Settings</b>

Current settings:
• Staking Rewards: {'✅ ON' if user['alert_rewards'] else '❌ OFF'}
• Incoming TX: {'✅ ON' if user['alert_tx_in'] else '❌ OFF'}
• Outgoing TX: {'✅ ON' if user['alert_tx_out'] else '❌ OFF'}

<b>Change settings:</b>
<code>/settings all</code> - Enable all alerts
<code>/settings rewards</code> - Toggle rewards only
<code>/settings tx_in</code> - Toggle incoming TX
<code>/settings tx_out</code> - Toggle outgoing TX
<code>/settings none</code> - Disable all alerts
"""
            self.send_message(chat_id, text)
            return
        
        # Handle setting changes
        args = args.lower().strip()
        
        if args == 'all':
            self.db.update_settings(chat_id, 1, 1, 1)
            self.send_message(chat_id, "✅ All alerts enabled!")
        elif args == 'none':
            self.db.update_settings(chat_id, 0, 0, 0)
            self.send_message(chat_id, "✅ All alerts disabled!")
        elif args == 'rewards':
            new_val = 0 if user['alert_rewards'] else 1
            self.db.update_settings(chat_id, new_val, user['alert_tx_in'], user['alert_tx_out'])
            self.send_message(chat_id, f"✅ Reward alerts {'enabled' if new_val else 'disabled'}!")
        elif args == 'tx_in':
            new_val = 0 if user['alert_tx_in'] else 1
            self.db.update_settings(chat_id, user['alert_rewards'], new_val, user['alert_tx_out'])
            self.send_message(chat_id, f"✅ Incoming TX alerts {'enabled' if new_val else 'disabled'}!")
        elif args == 'tx_out':
            new_val = 0 if user['alert_tx_out'] else 1
            self.db.update_settings(chat_id, user['alert_rewards'], user['alert_tx_in'], new_val)
            self.send_message(chat_id, f"✅ Outgoing TX alerts {'enabled' if new_val else 'disabled'}!")
        else:
            self.send_message(chat_id, "❌ Invalid option. Use /settings to see available options.")
    
    def handle_unsubscribe(self, chat_id: int):
        """Handle /unsubscribe command"""
        user = self.db.get_user(chat_id)
        
        if not user:
            self.send_message(chat_id, "You're not subscribed to any alerts.")
            return
        
        self.db.delete_user(chat_id)
        text = """
✅ <b>Successfully unsubscribed</b>

Your data has been deleted from our system.

You can always come back and use /start to set up alerts again.

Thank you for using IoTeX Alert Bot! 👋
"""
        self.send_message(chat_id, text)
        logger.info(f"User {chat_id} unsubscribed")
    
    def handle_help(self, chat_id: int):
        """Handle /help command"""
        text = """
📚 <b>IoTeX Alert Bot - Help</b>

<b>Features:</b>
• Real-time staking reward notifications
• Instant transaction alerts (send/receive)
• Support for both io... and 0x... addresses
• Customizable alert preferences

<b>Commands:</b>
/start - Start the bot and get instructions
/setaddress - Set/update your IoTeX address
/getaddress - View your saved address
/settings - Customize alert preferences
/unsubscribe - Stop all alerts and delete data
/help - Show this help message

<b>Examples:</b>
<code>/setaddress io1abc123...</code>
<code>/settings all</code>
<code>/settings rewards</code>

<b>Privacy:</b>
We only store your chat ID and address. We never ask for private keys or seed phrases.

<b>Support:</b>
For issues or questions, please contact the bot administrator.

⚡ Powered by IoTeX
"""
        self.send_message(chat_id, text)
    
    def process_updates(self):
        """Process incoming Telegram updates"""
        updates = self.get_updates()
        
        for update in updates:
            self.offset = update['update_id'] + 1
            
            if 'message' not in update:
                continue
            
            message = update['message']
            chat_id = message['chat']['id']
            text = message.get('text', '')
            
            if not text.startswith('/'):
                continue
            
            parts = text.split(maxsplit=1)
            command = parts[0].lower().replace(f'@{BOT_TOKEN.split(":")[0]}', '')
            args = parts[1] if len(parts) > 1 else ''
            
            try:
                if command == '/start':
                    self.handle_start(chat_id)
                elif command == '/setaddress':
                    self.handle_setaddress(chat_id, args)
                elif command == '/getaddress':
                    self.handle_getaddress(chat_id)
                elif command == '/settings':
                    self.handle_settings(chat_id, args)
                elif command == '/unsubscribe':
                    self.handle_unsubscribe(chat_id)
                elif command == '/help':
                    self.handle_help(chat_id)
                else:
                    self.send_message(chat_id, "Unknown command. Use /help to see available commands.")
            except Exception as e:
                logger.error(f"Error processing command {command}: {e}")
                self.send_message(chat_id, "An error occurred. Please try again later.")
    
    def format_timestamp(self, timestamp: int) -> str:
        """Format timestamp to local timezone"""
        dt = datetime.fromtimestamp(timestamp, tz=TIMEZONE)
        return dt.strftime('%Y-%m-%d %H:%M:%S %Z')
    
    def shorten_address(self, address: str) -> str:
        """Shorten address for display"""
        if len(address) > 15:
            return f"{address[:6]}...{address[-4:]}"
        return address
    
    def send_transaction_alert(self, chat_id: int, tx: Dict, user_address: str, is_incoming: bool):
        """Send transaction alert"""
        amount_iotx = float(tx.get('amount', 0)) / 1e18
        tx_hash = tx.get('hash', 'unknown')
        from_addr = tx.get('from', '').lower()
        to_addr = tx.get('to', '').lower()
        timestamp = int(tx.get('timestamp', 0))
        block_num = tx.get('blockNumber', 'unknown')
        
        explorer_url = f"https://iotexscan.io/tx/{tx_hash}"
        
        if is_incoming:
            emoji = "📥"
            direction = "Incoming Transaction"
            other_addr = from_addr
            label = "From"
        else:
            emoji = "📤"
            direction = "Outgoing Transaction"
            other_addr = to_addr
            label = "To"
        
        text = f"""
{emoji} <b>{direction}</b>

👤 <b>{label}:</b> <code>{self.shorten_address(other_addr)}</code>
💰 <b>Amount:</b> {amount_iotx:.4f} IOTX
🔗 <b>Transaction:</b> <a href="{explorer_url}">View on Explorer</a>
📦 <b>Block:</b> {block_num}
🕐 <b>Time:</b> {self.format_timestamp(timestamp)}
"""
        
        self.send_message(chat_id, text)
        logger.info(f"Sent {'incoming' if is_incoming else 'outgoing'} TX alert to {chat_id}")
    
    def send_reward_alert(self, chat_id: int, reward_info: Dict):
        """Send staking reward alert"""
        amount = reward_info.get('amount', 0)
        validator_name = reward_info.get('validator_name', 'Unknown')
        tx_hash = reward_info.get('tx_hash', 'unknown')
        
        explorer_url = f"https://iotexscan.io/tx/{tx_hash}"
        
        text = f"""
🎉 <b>Staking Reward Received!</b>

💰 <b>Amount:</b> {amount} IOTX
🏛 <b>Validator:</b> {validator_name}
🔍 <a href="{explorer_url}">View on Explorer</a>
"""
        
        self.send_message(chat_id, text)
        logger.info(f"Sent reward alert to {chat_id}")
    
    def monitor_transactions(self):
        """Monitor transactions for all users"""
        users = self.db.get_all_users()
        current_block = self.iotex_api.get_current_block()
        
        if not current_block:
            return
        
        for user in users:
            chat_id = user['chat_id']
            address = user['eth_address'] or user['io_address']
            
            if not address:
                continue
            
            last_block = self.db.get_last_block(chat_id)
            if not last_block:
                last_block = current_block - 100  # Start from 100 blocks ago
            
            # Only check confirmed blocks
            end_block = current_block - CONFIRMATIONS
            
            if last_block >= end_block:
                continue
            
            try:
                transactions = self.iotex_api.get_transactions(address, last_block + 1, end_block)
                
                for tx in transactions:
                    tx_hash = tx.get('hash')
                    
                    if not tx_hash or self.db.is_tx_processed(chat_id, tx_hash):
                        continue
                    
                    from_addr = tx.get('from', '').lower()
                    to_addr = tx.get('to', '').lower()
                    user_addr = address.lower()
                    
                    is_incoming = to_addr == user_addr and from_addr != user_addr
                    is_outgoing = from_addr == user_addr and to_addr != user_addr
                    
                    if is_incoming and user['alert_tx_in']:
                        self.send_transaction_alert(chat_id, tx, user_addr, True)
                        self.db.mark_tx_processed(chat_id, tx_hash)
                    elif is_outgoing and user['alert_tx_out']:
                        self.send_transaction_alert(chat_id, tx, user_addr, False)
                        self.db.mark_tx_processed(chat_id, tx_hash)
                
                self.db.update_last_block(chat_id, end_block)
                
            except Exception as e:
                logger.error(f"Error monitoring transactions for {chat_id}: {e}")

def run_bot():
    """Main bot loop"""
    db = Database(DB_PATH)
    iotex_api = IoTeXAPI(IOTEX_RPC_URL, IOTEX_GRAPHQL_URL)
    bot = TelegramBot(db, iotex_api)
    
    logger.info("Bot started successfully!")
    logger.info(f"Polling interval: {POLL_INTERVAL_SEC}s")
    logger.info(f"Confirmations required: {CONFIRMATIONS}")
    
    last_monitor = 0
    
    while True:
        try:
            # Process Telegram updates
            bot.process_updates()
            
            # Monitor transactions periodically
            current_time = time.time()
            if current_time - last_monitor >= POLL_INTERVAL_SEC:
                bot.monitor_transactions()
                last_monitor = current_time
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            time.sleep(5)

if __name__ == '__main__':
    run_bot()