# bot_minimal.py
import os, logging
from fastapi import FastAPI
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot_minimal")
logger.info("🚀 MINIMAL BOT STARTING")

app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive", "note": "minimal"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("bot_minimal:app", host="0.0.0.0", port=port, log_level="info")