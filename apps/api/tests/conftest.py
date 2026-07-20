import os

os.environ["DATABASE_URL"] = "sqlite:////tmp/home-media-api-tests.db"
os.environ["TELEGRAM_ALLOWED_USER_IDS"] = "101,202"
os.environ["AUTOMATION_WEBHOOK_TOKEN"] = "test-webhook-token"
os.environ["REDIS_URL"] = "redis://localhost:6399/0"
