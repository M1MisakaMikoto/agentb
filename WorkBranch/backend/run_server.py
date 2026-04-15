import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

print("Starting server...", flush=True)

import uvicorn
from app import app

print("App imported", flush=True)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
