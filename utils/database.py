import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

db_url = os.getenv('DATABASE_URL', "postgresql+psycopg://myuser:mypassword@db:5432/postgres")
engine = create_engine(db_url)
sessionLocal = sessionmaker(autocommit=False, autoflush=False,bind=engine)

from redis import Redis

REDIS_URL = os.getenv("REDIS_URL", "redis://myuser:mypassword@redis:6379/0")

redis_client = Redis.from_url(REDIS_URL, decode_responses=True)

def get_redis() -> Redis:
    return redis_client


