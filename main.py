import os
import asyncio
from telegram.ext import Application, CommandHandler
import requests
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Your bot token from environment variable
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

async def start(update, context):
    await update.message.reply_text('Hello! Bot is running on Railway!')

async def check_transaction(update, context):
    # Your transaction checking logic here
    await update.message.reply_text('Checking transactions...')

def main():
    if not TOKEN:
        logger.error("No TELEGRAM_BOT_TOKEN found in environment variables")
        return
    
    # Create Application
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("check", check_transaction))
    
    # Start the bot
    logger.info("Starting bot...")
    application.run_polling()

if __name__ == '__main__':
    main()