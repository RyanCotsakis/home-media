from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.models import Base
from app.db import engine
from app.routes import router


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Alembic migrations will replace this bootstrap before the first production upgrade.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Home Media API", lifespan=lifespan)
app.include_router(router)
