from datetime import datetime,timezone
import string, random
from typing import Optional, Annotated, cast
from fastapi import APIRouter, Depends, Path, Body, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, StreamingResponse, Response
from sqlalchemy.exc import IntegrityError
from utils.database import sessionLocal, engine, get_redis, Redis
from starlette import status
from utils import database_models
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from .auth import get_current_user, decode_user_from_token
from bs4 import BeautifulSoup
import httpx
import json
import base64
import io
from utils.AWShelper import generate_qr_code, upload_qr_to_s3
from security.safebrowsing import check_url_with_google_safe_browsing, classify_url_with_openai

from pydantic import BaseModel, HttpUrl, Field, constr
class LinkRequest(BaseModel):
    alias: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{3,30}$",
        description="Optional alias of 3–30 chars containing only letters, numbers, _ or -",)
    title: Optional[str] = None
    original_url: HttpUrl # validates http/https
    generate_qr: Optional[bool] = False

    class Config:
        json_schema_extra = {
            'example': {
                'alias': 'your-custom-alias',
                'title': 'Title (Optional)',
                'original_url': 'http://example.com/resource',
                'generate_qr': True
            }
        } 

class LinkUpdateRequest(BaseModel):
    title: Optional[str] = None
    tags: Optional[list[str]] = None

    class Config:
        json_schema_extra = {
            'example': {
                'title': 'New Link Title',
                'tags': ['tag1', 'tag2']
            }
        }
#------------------------------------
API_URL = "localhost:8000/"

CACHE_TTL_SECONDS = 300  # 5 minutes
QR_CACHE_TTL_SECONDS = 3600  # 1 hour

chars = string.ascii_letters + string.digits

def link_to_dict(link: database_models.Links) -> dict:
    return {
        "id": link.id,
        "alias": link.alias,
        "original_url": link.original_url,
        "title": link.title,
        "short_code": link.short_code,
        "short_url": link.short_url,
        "clicks": link.clicks,
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "qr_code_path": link.qr_code_path,
    }

def getString():
     return ''.join(random.choice(chars) for _ in range(6))

router = APIRouter(
    tags = ['links']
)

def get_db():
    db = sessionLocal()
    try:
        yield db
    finally:
        db.close()
    
def links_user(user_id: int) -> str:
    return f"user:{user_id}:links"

def link_key(key: str) -> str:
    return f"link:{key}"

def link_qr_key(key: str) -> str:
    return f"link_qr:{key}"

# Counter per link ID
def click_counter_key(link_id: int) -> str:
    return f"click_count:{link_id}"

# Set of link IDs that currently have pending deltas to flush
DIRTY_SET_KEY = "click_dirty_links"

db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[dict,Depends(get_current_user)]

@router.get("/links")
def get_all_links(user: user_dependency, db: db_dependency, redis: Redis = Depends(get_redis)):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')
    
    user_id = user.get('id')
    assert user_id is not None
    cache_key = links_user(user_id)# 
    cached_links = redis.get(cache_key)  
    if cached_links:
        return json.loads(cast(str, cached_links))

    db_links = db.query(database_models.userLinks, database_models.Links).join(
        database_models.Links, 
        database_models.userLinks.link_id == database_models.Links.id,
    ).filter(database_models.userLinks.user_id == user_id).all()
    
    data = [
        user_link_view_dict(ul, link)
        for (ul, link) in db_links
    ]
    redis.set(cache_key, json.dumps(data), ex=CACHE_TTL_SECONDS)

    return data

@router.get("/links/qrcode/")
def get_link_qrcode(user: user_dependency, db: db_dependency, key:str, redis: Redis = Depends(get_redis)):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')
    
    key = key.replace("http://","").replace("https://","").replace(API_URL,"")

    db_link = db.query(database_models.Links).filter(
        (database_models.Links.short_code == key)|
        (database_models.Links.alias == key)).first()
    if not db_link: 
        raise HTTPException(404,"Link not found")
    
    qr_code_img = generate_qr_code(f"http://{API_URL}{key}")
    qr_s3_url = upload_qr_to_s3(key, qr_code_img.getvalue())
    db_link.qr_code_path = qr_s3_url
    db.commit()

    redis.set(link_qr_key(key), qr_code_img.getvalue(), ex=QR_CACHE_TTL_SECONDS)
    redis.delete(links_user(user.get('id'))) #type: ignore
    return {'qr_code_path': qr_s3_url}
    
#ShortToLong. This endpoint is the last one to avoid conflict with other /links/ endpoints
@router.get("/{key}")
def go_to_link( db: db_dependency, key:str, redis: Redis = Depends(get_redis)):

    data = get_link_by_key(db, redis, key, update_clicks=True)
    return RedirectResponse(url=data['original_url'])

def get_link_by_key(db, redis, key: str, update_clicks: bool = False):
    cache_key = link_key(key) 
    cached_link =  redis.get(cache_key)
    if cached_link:
        data = json.loads(cast(str, cached_link))
    else:
    
        db_link = db.query(database_models.Links).filter(
            (database_models.Links.short_code == key)|
            (database_models.Links.alias == key)).first()
        if not db_link: 
            raise HTTPException(404,"Link not found")

        data = link_to_dict(db_link)
        redis.set(cache_key, json.dumps(data), ex=CACHE_TTL_SECONDS)
    if update_clicks:
        data['clicks'] += 1
        redis.set(cache_key, json.dumps(data), ex=CACHE_TTL_SECONDS) 
        redis.incr(click_counter_key(data['id']))  
        redis.sadd(DIRTY_SET_KEY, data['id'])

    return data

#LongToShort
@router.post("/shorten/",status_code = status.HTTP_201_CREATED)
async def shorten_link(user: user_dependency, db: db_dependency, link: LinkRequest, redis: Redis = Depends(get_redis)):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')
    
    link_model = await create_link_for_user(db, user, link)

    data = link_to_dict(link_model)

    # Invalidate user's list
    user_id = user.get('id')
    assert user_id is not None
    redis.delete(links_user(user_id))
    # Populate single-link cache
    redis.set(link_key(link_model.short_code if link_model.short_code else link_model.alias 
                             ), json.dumps(data), ex=CACHE_TTL_SECONDS) #  

    return data

async def link_safety_check(url: str):
    threat = await check_url_with_google_safe_browsing(url)
    if threat:
        raise HTTPException(400, detail="The provided URL is flagged as unsafe.")
    
    classify = await classify_url_with_openai(url)
    category = classify["category"]

    if category in ("spam", "scam_or_phishing", "extremely_high_risk"):
        raise HTTPException(400, detail="The provided URL is flagged as spam or unsafe.")

async def create_link_for_user(db: Session, user, link: LinkRequest) -> database_models.Links:
    user_id = user.get('id')
    if not user_id:
        raise HTTPException(401, detail='Authentication Failed.')

    timestamp = datetime.now(timezone.utc)
    long_url = str(link.original_url)
    
    #check for existing link with same URL
    link_check = db.query(database_models.Links).filter(
        database_models.Links.original_url == long_url).first()
    if link_check:
        user_link_check = db.query(database_models.userLinks).filter(
            database_models.userLinks.user_id == user_id,
            database_models.userLinks.link_id == link_check.id,
        ).first()
        if user_link_check:
            return link_check  # User already has this link

    if link.alias: #custom alias provided
        
        link_check = db.query(database_models.Links).filter(
                (database_models.Links.short_code == link.alias)|
                (database_models.Links.alias == link.alias)).first()
        if link_check:
            if long_url != link_check.original_url:
                raise HTTPException(409,detail="A different Link already uses this alias.")
            #same link already exists for different user, just link it to this user
            add_link_to_user(user_id, link_check.id, link.alias,
                             link.title if link.title else link_check.title, db)
            return link_check
        
        await link_safety_check(long_url)
        title = await fetch_title(long_url)
        link_model = database_models.Links(
        original_url = long_url, 
        title = title, short_code = None, alias = link.alias,
        created_at=timestamp, short_url = API_URL + link.alias)
    
    else: #no custom alias
        #check for existing link with same URL
        link_check = db.query(database_models.Links).filter(
            database_models.Links.original_url == long_url, 
            database_models.Links.short_code != None).first()
        if link_check:
            #same link already exists for different user, just link it to this user
            add_link_to_user(user_id, link_check.id, link_check.short_code,
                             link.title if link.title else link_check.title, db)
            return link_check
    
        short_code = getString()
        
        while db.query(database_models.Links).filter(
            database_models.Links.short_code == short_code).first():
            short_code = getString() 
        
        await link_safety_check(long_url)
        title = await fetch_title(long_url)
        link_model = database_models.Links(
        original_url = long_url, 
        title = title, short_code = short_code, alias = None,
        created_at=timestamp, short_url = API_URL + short_code)
    
    key = link_model.short_code if link_model.short_code else link_model.alias
    if link.generate_qr and not link_model.qr_code_path:
        #generates QR code and uploads to AWS S3
        qr_code_img = generate_qr_code(f"http://{API_URL}{key}")
        qr_s3_url = upload_qr_to_s3(key, qr_code_img.getvalue())
        link_model.qr_code_path = qr_s3_url
    db.add(link_model)
    db.commit()
    add_link_to_user(user_id, link_model.id, key,
                     link.title if link.title else title, db)
    db.refresh(link_model)

    return link_model


@router.put("/links/{key}/",status_code = status.HTTP_202_ACCEPTED)
def update_link(user: user_dependency, db: db_dependency, 
                   update:LinkUpdateRequest, key:str, redis: Redis = Depends(get_redis)):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')
    
    key = key.replace("http://","").replace("https://","").replace(API_URL,"")
    user_id = user.get('id')
    assert user_id is not None
    db_link = db.query(database_models.userLinks).filter(
        database_models.userLinks.key == key, 
        database_models.userLinks.user_id == user_id).first()
    if not db_link:
        raise HTTPException(404,"Link not found for this user")
    if update.title is not None:
        db_link.title = update.title
    if update.tags is not None:
        db_link.tags = update.tags
    db.commit()

    # Invalidate caches
    redis.delete(links_user(user_id)) 
    return "Link updated"

@router.delete("/by_key/",status_code=status.HTTP_200_OK)
async def delete_link_by_key(user: user_dependency, key:str, db:Session = Depends(get_db), redis: Redis = Depends(get_redis)):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')

    key = key.replace("http://","").replace("https://","").replace(API_URL,"")
    user_id = user.get('id')
    assert user_id is not None
    db_link = db.query(database_models.Links).filter(
        (database_models.Links.short_code == key)|
        (database_models.Links.alias == key)).first()
    if db_link:
        user_link = db.query(database_models.userLinks).filter(
        database_models.userLinks.user_id == user_id,
        database_models.userLinks.link_id == db_link.id,
        ).first()

        if not user_link:
            # Link exists globally, but this user doesn't have it in their list
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found for this user")

        # Delete ONLY the user_link row
        db.delete(user_link)
        db.commit()
        # Invalidate caches
        redis.delete(links_user(user_id))

        remaining = db.query(database_models.userLinks).filter(
            database_models.userLinks.link_id == db_link.id
        ).count()

        if remaining == 0:
            db.delete(db_link)
            db.commit()

            redis.delete(link_key(db_link.short_code))
            redis.delete(link_key(db_link.alias))

        return "Link deleted"
    #return "Link not found"
    raise HTTPException(404,"Link not found")

@router.get("/link/title/",status_code = status.HTTP_200_OK)
async def get_link_title(user: user_dependency, url: str):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')
    
    # threat = check_url_with_google_safe_browsing(url)
    # if threat:
    #     return Response(content="The provided URL is flagged as unsafe" , media_type="text/plain")
    
    # classify = classify_url_with_openai(url)
    # category = classify["category"]

    # if category in ("spam", "scam_or_phishing", "extremely_high_risk"):
    #     return Response(content="The provided URL is flagged as spam or unsafe" ,
    #                      media_type="text/plain")
    
    return Response(content=await fetch_title(url) , media_type="text/plain")

async def fetch_title(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.RequestError as exc:
        #raise HTTPException(status_code=400, detail=f"Error fetching URL: {exc}") from exc
        return "Failed to Fetch Title"

    if resp.status_code != 200:
        #raise HTTPException(status_code=resp.status_code,detail=f"Failed to fetch URL (status {resp.status_code})")
        return "Failed to Fetch Title"

    # Parse HTML
    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("title")

    if not title_tag or not title_tag.string:
        return "No Title"

    return title_tag.string.strip()

@router.websocket("/ws/batch-upload/")
async def ws_batch_upload(websocket: WebSocket, db: Session = Depends(get_db), redis: Redis = Depends(get_redis)):
    token = websocket.query_params.get("token")
    if not token:
        # Try header fallback (client must explicitly add this)
        token = websocket.headers.get("Authorization")
        if token and token.startswith("Bearer "):
            token = token.split(" ", 1)[1]

    if not token:
        await websocket.close(code=1008, reason="Authentication failed")
        return

    try:
        user = decode_user_from_token(token)  # already returns username/id/role
    except HTTPException:
        await websocket.close(code=1008, reason="Authentication failed")
        return

    user_id = user["id"]
    if not user_id:
        await websocket.close(code=1008, reason="Authentication failed")
        return

    await websocket.accept()

    processed = 0
    total = None  # you can fill this from "start" message if client sends it
    index = 0

    try:
        while True:
            message = await websocket.receive_json()

            mtype = message.get("type")

            if mtype == "start":
                total = message.get("total")
                await websocket.send_json({
                    "type": "started",
                    "total": total,
                })

            elif mtype == "item":
                index += 1
                raw = message.get("data") or {}
                try:
                    # Validate payload using your Link Pydantic model
                    link = LinkRequest(**raw)
                    link_model = await create_link_for_user(db, user, link)
                    redis.delete(links_user(user_id))
                    processed += 1
                    await websocket.send_json({
                        "type": "item_result",
                        "index": index,
                        "status": "ok",
                        "id": link_model.id,
                        "short_url": link_model.short_url,
                        "short_code": link_model.short_code,
                        "alias": link_model.alias,
                    })

                except HTTPException as e:
                    # Business logic error
                    await websocket.send_json({
                        "type": "item_result",
                        "index": index,
                        "status": "error",
                        "code": e.status_code,
                        "detail": e.detail,
                    })
                except Exception as e:
                    # Unexpected error
                    await websocket.send_json({
                        "type": "item_result",
                        "index": index,
                        "status": "error",
                        "code": 500,
                        "detail": str(e),
                    })

                # Optional: also send progress
                if total:
                    await websocket.send_json({
                        "type": "progress",
                        "processed": processed,
                        "total": total,
                    })

            elif mtype == "finish":
                await websocket.send_json({
                    "type": "finished",
                    "processed": processed,
                    "total": total,
                })
                await websocket.close()
                return
            
            elif mtype == "cancel":
                await websocket.send_json({
                    "type": "cancelled",
                    "processed": processed,
                    "total": total,
                })
                await websocket.close()
                return

            else:
                # Unknown message type
                await websocket.send_json({
                    "type": "error",
                    "detail": f"Unknown message type: {mtype}",
                })

    except WebSocketDisconnect:
        # Client ended connection
        return
        

def add_link_to_user(user_id: int, link_id: int, key: str, title: str, db: db_dependency):
    user_link = database_models.userLinks(
        user_id=user_id,
        link_id=link_id,
        key=key,
        title=title
    )
    db.add(user_link)
    db.commit()

def user_link_view_dict(
    ul: database_models.userLinks,
    link: database_models.Links,
) -> dict:
    return {
        "user_link_id": ul.id,
        "id": link.id,
        "short_code": link.short_code,
        "alias": link.alias,
        "short_url": link.short_url,
        "original_url": link.original_url,
        "title": ul.title or link.title,   # per-user override
        "default_title": link.title,
        "tags": ul.tags or [],
        "clicks": link.clicks,
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "qr_code_path": link.qr_code_path,
    }