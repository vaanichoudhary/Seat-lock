from fastapi import FastAPI

app= FastAPI(title="Seat Lock")
@app.get('/health')
async def health():
    return {"status": "ok"}
