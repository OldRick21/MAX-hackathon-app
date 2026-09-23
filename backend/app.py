from fastapi import FastAPI

app = FastAPI()

@app.get("/api/v1/health")
def health():
	return {"status": "ok"}
