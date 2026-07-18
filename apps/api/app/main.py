from fastapi import FastAPI

app = FastAPI(
    title="Home Platform API",
    version="0.1.0",
)

@app.get("/")
def root():
    return {
        "message": "Home Platform API is running!"
    }

@app.get("/health")
def health():
    return {
        "status": "healthy"
    }