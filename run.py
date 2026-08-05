import uvicorn
import os

if __name__ == "__main__":
    # Port 8003 matches frontend API_BASE in api.ts
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8003,
        reload=True,
        log_level="info",
    )
