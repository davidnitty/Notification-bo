#!/bin/bash

# IoTeX Telegram Alert Bot - Setup Script for Railway
# This script helps you deploy the bot to Railway

set -e

echo "🚀 IoTeX Telegram Alert Bot - Railway Setup"
echo "============================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo -e "${RED}❌ Git is not installed. Please install git first.${NC}"
    exit 1
fi

# Check if Railway CLI is installed
if ! command -v railway &> /dev/null; then
    echo -e "${YELLOW}⚠️  Railway CLI is not installed.${NC}"
    echo "Would you like to install it now? (y/n)"
    read -r response
    if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        echo "Installing Railway CLI..."
        if command -v npm &> /dev/null; then
            npm install -g @railway/cli
        elif command -v curl &> /dev/null; then
            curl -fsSL https://railway.app/install.sh | sh
        else
            echo -e "${RED}❌ Cannot install Railway CLI. Please install manually.${NC}"
            exit 1
        fi
    else
        echo -e "${YELLOW}You can deploy manually using GitHub. See README.md${NC}"
        exit 0
    fi
fi

echo ""
echo "✅ Prerequisites checked!"
echo ""

# Initialize git repo if not already
if [ ! -d .git ]; then
    echo "📦 Initializing Git repository..."
    git init
    git add .
    git commit -m "Initial commit - IoTeX Alert Bot"
    git branch -M main
    echo -e "${GREEN}✅ Git repository initialized${NC}"
else
    echo -e "${GREEN}✅ Git repository already exists${NC}"
fi

echo ""
echo "🔐 Railway Authentication"
echo "========================"
echo ""

# Login to Railway
echo "Logging in to Railway..."
railway login

echo ""
echo "🎯 Project Setup"
echo "==============="
echo ""

# Initialize Railway project
if [ ! -f railway.json ]; then
    echo -e "${RED}❌ railway.json not found. Please ensure all files are in place.${NC}"
    exit 1
fi

echo "Initializing Railway project..."
railway init

echo ""
echo "📤 Deploying to Railway"
echo "======================"
echo ""

# Deploy
echo "Deploying your bot..."
railway up

echo ""
echo -e "${GREEN}✅ Deployment initiated!${NC}"
echo ""

# Set environment variables
echo "🔧 Setting Environment Variables"
echo "================================"
echo ""

BOT_TOKEN="7831036263:AAHSisyLSr5bSwfJ2jGXasRfLcRluo2y5gk"

echo "Setting TELEGRAM_BOT_TOKEN..."
railway variables set TELEGRAM_BOT_TOKEN="$BOT_TOKEN"

echo "Setting IOTEX_RPC_URL..."
railway variables set IOTEX_RPC_URL="https://babel-api.mainnet.iotex.io"

echo "Setting IOTEX_GRAPHQL_URL..."
railway variables set IOTEX_GRAPHQL_URL="https://analyser-api.iotex.io/graphql"

echo "Setting CONFIRMATIONS..."
railway variables set CONFIRMATIONS="3"

echo "Setting POLL_INTERVAL_SEC..."
railway variables set POLL_INTERVAL_SEC="20"

echo "Setting TZ..."
railway variables set TZ="Africa/Lagos"

echo "Setting DB_PATH..."
railway variables set DB_PATH="/app/data/iotex_bot.db"

echo ""
echo -e "${GREEN}✅ Environment variables set!${NC}"
echo ""

echo "⚠️  IMPORTANT: Add Persistent Volume"
echo "===================================="
echo ""
echo "To prevent data loss, you need to add a volume:"
echo "1. Go to your Railway dashboard"
echo "2. Click on your service"
echo "3. Go to 'Volumes' tab"
echo "4. Add a new volume:"
echo "   - Mount Path: /app/data"
echo "   - Size: 1 GB"
echo ""
echo "Press Enter when you've added the volume..."
read -r

echo ""
echo "🎉 Setup Complete!"
echo "=================="
echo ""
echo "Your bot is now deploying to Railway!"
echo ""
echo "Next steps:"
echo "1. ✅ Volume added (you just did this)"
echo "2. 📊 Check deployment logs: railway logs"
echo "3. 🤖 Test your bot on Telegram:"
echo "   - Open Telegram"
echo "   - Search for your bot"
echo "   - Send /start"
echo ""
echo "Useful commands:"
echo "  railway logs          - View logs"
echo "  railway status        - Check deployment status"
echo "  railway open          - Open project in browser"
echo "  railway variables     - List environment variables"
echo ""
echo -e "${GREEN}Happy monitoring! 🚀${NC}"