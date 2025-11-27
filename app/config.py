import os
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from .env file with explicit path
load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

# WATI Configuration
WATI_API_KEY = os.getenv("WATI_API_KEY")
WATI_API_URL = os.getenv("WATI_API_URL")

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_AGENT_ID = os.getenv("OPENAI_AGENT_ID")

# Webhook Configuration
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# Database Configuration
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

# Gmail Configuration
GMAIL_SENDER_EMAIL = os.getenv("GMAIL_SENDER_EMAIL")
GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID")
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET")
GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_REFRESH_TOKEN")

# ManyChat Configuration
#
# Strict separation of Facebook and Instagram API keys.
# MANYCHAT_API_URL defaults to official ManyChat API base if not provided.
MANYCHAT_API_URL = os.getenv('MANYCHAT_API_URL', 'https://api.manychat.com')
MANYCHAT_API_KEY = os.getenv('MANYCHAT_API_KEY')  # Facebook
MANYCHAT_INSTAGRAM_API_KEY = os.getenv('MANYCHAT_INSTAGRAM_API_KEY')  # Instagram

# Public media base URL used to build public links for media sent via ManyChat
PUBLIC_MEDIA_BASE_URL = os.getenv('PUBLIC_MEDIA_BASE_URL', 'https://media.example.com')

# Flex Tier Configuration
# Enable/disable Flex tier for cost savings (50% cheaper but higher latency)
FLEX_ENABLED = os.getenv("FLEX_ENABLED", "true").lower() == "true"
FLEX_TIMEOUT_SECONDS = int(os.getenv("FLEX_TIMEOUT_SECONDS", "120"))  # 2 minutes
