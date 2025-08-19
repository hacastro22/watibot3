import os
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from .env file
load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

# WATI Configuration
WATI_API_KEY = os.getenv('WATI_API_KEY')
WATI_API_URL = os.getenv('WATI_API_URL', 'https://app.wati.io/api/v1')

# OpenAI Configuration (Current Production)
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_AGENT_ID = os.getenv('OPENAI_AGENT_ID')

# Vertex AI Configuration (Migration Target)
USE_VERTEX_AI = os.getenv('USE_VERTEX_AI', 'false').lower() == 'true'
GOOGLE_CLOUD_PROJECT_ID = os.getenv('GOOGLE_CLOUD_PROJECT_ID')
VERTEX_AI_LOCATION = os.getenv('VERTEX_AI_LOCATION', 'us-central1')
VERTEX_AGENT_ENGINE_ID = os.getenv('VERTEX_AGENT_ENGINE_ID')
GOOGLE_APPLICATION_CREDENTIALS = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

# Webhook Configuration
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')

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

# Database Configuration
DB_HOST = os.getenv('DB_HOST')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')

# CompraClick Configuration
COMPRACLICK_EMAIL = os.getenv('COMPRACLICK_EMAIL')
COMPRACLICK_PASSWORD = os.getenv('COMPRACLICK_PASSWORD')

# BAC Banking Configuration
BAC_USERNAME = os.getenv('BAC_USERNAME')
BAC_PASSWORD = os.getenv('BAC_PASSWORD')
