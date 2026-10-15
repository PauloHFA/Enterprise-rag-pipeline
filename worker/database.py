import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import Base as Base

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://raguser:ragpass@localhost:5432/ragdb")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
