from dotenv import load_dotenv
import os

load_dotenv()

class Settings:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

    MONGO_URL = os.getenv("MONGO_URL")
    DB_NAME = os.getenv("DB_NAME")

    FRONTEND_URL = os.getenv("FRONTEND_URL")
    
settings = Settings()
