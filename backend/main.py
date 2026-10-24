from fastapi import FastAPI

app = FastAPI(title="Aura Skincare Support API")


@app.get("/health")
def health_check():
    return {"status": "ok"}
