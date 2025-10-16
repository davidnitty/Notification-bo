import os
import telebot
import requests
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Your bot token - try environment variable first, then fallback to direct token
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '7831036263:AAHSisyLSr5bSwfJ2jGXasRfLcRluo2y5gk')

if not TOKEN:
    logger.error("No TELEGRAM_BOT_TOKEN found!")
    exit(1)

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Hello! Bot is running on Railway!")

@bot.message_handler(commands=['check'])
def check_transaction(message):
    bot.reply_to(message, "Checking transactions...")

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, "I received your message!")

logger.info("Starting bot polling...")
logger.info(f"Bot token: {TOKEN[:10]}...")  # Log first 10 chars for verification
bot.infinity_polling()
