from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from app.config import settings

engine = create_engine(settings.database_url, echo=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Provide one SQLAlchemy session per request and always close it."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
