import time
from redis import Redis
from sqlalchemy.orm import Session
from utils import database_models
from .database import redis_client, sessionLocal

DIRTY_SET_KEY = "click_dirty_links"

def click_counter_key(link_id: int) -> str:
    return f"click_count:{link_id}"

def flush_clicks_once(batch_size: int = 100):
    """
    Flush up to `batch_size` dirty links from Redis to Postgres.
    """
    # Get up to `batch_size` link IDs from dirty set
    dirty_ids = redis_client.spop(DIRTY_SET_KEY, batch_size)  # returns single or list or None

    if not dirty_ids:
        return

    # Normalize to list
    if isinstance(dirty_ids, str):
        dirty_ids = [dirty_ids]

    # Read counters and reset them
    increments = {}
    pipe = redis_client.pipeline()
    for id_str in dirty_ids:
        pipe.get(click_counter_key(int(id_str)))
    counts = pipe.execute()

    for id_str, val in zip(dirty_ids, counts):
        if val is None:
            continue
        delta = int(val)
        if delta <= 0:
            continue
        increments[int(id_str)] = delta

    if not increments:
        return

    # Apply increments in DB
    db: Session = sessionLocal()
    try:
        for link_id, delta in increments.items():
            db.query(database_models.Links).filter(
                database_models.Links.id == link_id
            ).update(
                {database_models.Links.clicks: database_models.Links.clicks + delta},
                synchronize_session=False,
            )
        db.commit()
    except Exception as e:
        db.rollback()
        # If something goes wrong, re-add these IDs back to the dirty set
        for link_id in increments.keys():
            redis_client.sadd(DIRTY_SET_KEY, link_id)
        raise
    finally:
        db.close()

    # Zero-out the counters after successful flush
    pipe = redis_client.pipeline()
    for link_id in increments.keys():
        pipe.delete(click_counter_key(link_id))
    pipe.execute()

def main_loop():
    while True:
        flush_clicks_once(batch_size=500)
        time.sleep(5)  

if __name__ == "__main__":
    main_loop()

