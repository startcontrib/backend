import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """Base configuration variables."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'a_default_secret_key_for_dev') 
    GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
    DATABASE_URL = os.environ.get('DATABASE_URL') # For future DB use

    # Basic validation
    if not GITHUB_TOKEN:
        print("Warning: GITHUB_TOKEN environment variable not set.")
    if not GEMINI_API_KEY:
        print("Warning: GEMINI_API_KEY environment variable not set.")

AppConfig = Config()