from fastapi import Depends, FastAPI

from app.auth import CurrentUser, require_current_user

app = FastAPI()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/me")
async def current_user(user: CurrentUser = Depends(require_current_user)) -> CurrentUser:
    return user
