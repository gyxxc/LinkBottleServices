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
from utils.qrcode import generate_qr_code

from pydantic import BaseModel, HttpUrl, Field, constr
class LinkRequest(BaseModel):
    alias: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{3,30}$",
        description="Optional alias of 3–30 chars containing only letters, numbers, _ or -",)
    title: Optional[str] = None
    original_url: HttpUrl # validates http/https

    class Config:
        json_schema_extra = {
            'example': {
                'alias': 'your-custom-alias',
                'title': 'Title (Optional)',
                'original_url': 'http://example.com/resource'
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
        "user_id": link.user_id,
        "title": link.title,
        "short_code": link.short_code,
        "short_url": link.short_url,
        "clicks": link.clicks,
        "created_at": link.created_at.isoformat() if link.created_at else None,
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
    user_id = user.get('id')
    assert user_id is not None

    qr_cache_key = link_qr_key(key)
    cached_qr = redis.get(qr_cache_key)
    if cached_qr:
        qr_code_data = base64.b64decode(cast(str, cached_qr))
        return StreamingResponse(io.BytesIO(qr_code_data), media_type="image/png")


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
    
    qr_code_img = generate_qr_code('http://'+ data['short_url'])

    # Cache the QR code image in base64
    qr_code_base64 = base64.b64encode(qr_code_img.getvalue()).decode('ascii')
    redis.set(qr_cache_key, qr_code_base64, ex=QR_CACHE_TTL_SECONDS)

    return StreamingResponse(qr_code_img, media_type="image/png")

    
#ShortToLong. This endpoint is the last one to avoid conflict with other /links/ endpoints
@router.get("/{key}")
def go_to_link( db: db_dependency, key:str, redis: Redis = Depends(get_redis)):

    cache_key = link_key(key) 
    cached_link = redis.get(cache_key)
    if cached_link:
        data = json.loads(cast(str, cached_link))
        # Update clicks in cache
        data['clicks'] += 1
        redis.set(cache_key, json.dumps(data), ex=CACHE_TTL_SECONDS) 
        redis.incr(click_counter_key(data['id']))  
        redis.sadd(DIRTY_SET_KEY, data['id'])  

        return RedirectResponse(data['original_url']) 
    
    db_link = db.query(database_models.Links).filter(
        (database_models.Links.short_code == key)|
        (database_models.Links.alias == key)).first()
    
    if not db_link:
        raise HTTPException(404,"Link not found")

    cache_key = link_key(key)#  
    data = link_to_dict(db_link)
    # Update cache
    redis.set(cache_key, json.dumps(data), ex=CACHE_TTL_SECONDS)
    redis.incr(click_counter_key(db_link.id))  #
    redis.sadd(DIRTY_SET_KEY, db_link.id)  #
    #delete cache for user links list
    if db_link.user_id: #  
        redis.delete(links_user(db_link.user_id))  #  

    return RedirectResponse(db_link.original_url) #  
    
#LongToShort
@router.post("/shorten/",status_code = status.HTTP_201_CREATED)
async def shorten_link(user: user_dependency, db: db_dependency, link: LinkRequest, redis: Redis = Depends(get_redis)):
    link_model = await create_link_for_user(db, user, link)

    data = link_to_dict(link_model)

    # Invalidate user's list
    redis.delete(links_user(link_model.user_id))
    # Populate single-link cache
    redis.set(link_key(link_model.short_code if link_model.short_code else link_model.alias 
                             ), json.dumps(data), ex=CACHE_TTL_SECONDS) #  

    return data

async def create_link_for_user(db: Session, user, link: LinkRequest) -> database_models.Links:
    user_id = user.get('id')
    if not user_id:
        raise HTTPException(401, detail='Authentication Failed.')

    timestamp = datetime.now(timezone.utc)
    
    #check for existing link with same URL
    link_check = db.query(database_models.Links).filter(
        (database_models.Links.original_url == str(link.original_url)),
        (database_models.Links.user_id == user_id)).first()
    if link_check:
        raise HTTPException(409,detail="You already have a link for this URL.")

    if link.alias: #custom alias provided
        
        link_check = db.query(database_models.Links).filter(
                (database_models.Links.short_code == link.alias)|
                (database_models.Links.alias == link.alias)).first()
        if link_check:
            if str(link.original_url) != link_check.original_url:
                raise HTTPException(409,detail="A different Link already uses this alias.")
            if link_check.user_id == user_id:
                raise HTTPException(409,detail="You have already created this link.")
            #same link already exists for different user, just link it to this user
            add_link_to_user(user_id, link_check.id, link.title if link.title else link_check.title, db)
            return link_check
        
        title = await fetch_title(str(link.original_url))
        link_model = database_models.Links(
        original_url = str(link.original_url), user_id = user_id, 
        title = title, short_code = None, alias = link.alias,
        created_at=timestamp, short_url = API_URL + link.alias)
    
    else: #no custom alias
        #check for existing link with same URL
        link_check = db.query(database_models.Links).filter(
            database_models.Links.original_url == str(link.original_url)).first()
        if link_check:
            #same link already exists for different user, just link it to this user
            add_link_to_user(user_id, link_check.id, link.title if link.title else link_check.title, db)
            return link_check
    
        short_code = getString()
        
        while db.query(database_models.Links).filter(
            database_models.Links.short_code == short_code).first():
            short_code = getString() 
        
        title = await fetch_title(str(link.original_url))
        link_model = database_models.Links(
        original_url = str(link.original_url), user_id = user_id, 
        title = title, short_code = short_code, alias = None,
        created_at=timestamp, short_url = API_URL + short_code)
    

    db.add(link_model)
    db.commit()
    add_link_to_user(user_id, link_model.id, link.title if link.title else title, db)
    db.refresh(link_model)

    return link_model


# @router.put("/links/",status_code = status.HTTP_202_ACCEPTED)
# def update_link(user: user_dependency, db: db_dependency, 
#                    link:LinkRequest, key:str, redis: Redis = Depends(get_redis)):
#     if not user:
#         raise HTTPException(401, detail='Authentication Failed.')
    
#     key = key.replace("http://","").replace("https://","").replace(API_URL,"")
#     user_id = user.get('id')
#     assert user_id is not None
#     db_link = db.query(database_models.Links).filter(
#         (database_models.Links.short_code == key),
#         database_models.Links.user_id == user_id).first()
#     if db_link:
#         link_check = db.query(database_models.Links).filter(
#             (database_models.Links.alias == link.alias)|(database_models.Links.short_code == link.alias)
#             |((database_models.Links.original_url == str(link.original_url)) & (database_models.Links.user_id == user_id)), 
#             database_models.Links.id != db_link.id
#             ).first()
#         if link_check:
#             raise HTTPException(409,detail="Another link with same alias or URL already exists.")

#         db_link.title = link.title if link.title else db_link.title
#         db_link.alias = link.alias if link.alias else db_link.alias
#         db_link.original_url = str(link.original_url)
#         db.commit()
#         db.refresh(db_link)
  
#         cache_key = link_key(db_link.short_code) 
#         data = link_to_dict(db_link)
#         # Invalidate caches
#         redis.delete(links_user(user_id))
#         # Populate single-link cache
#         redis.set(cache_key, json.dumps(data), ex=CACHE_TTL_SECONDS)

#         return "Link Updated"
#     #return "Link not found"
#     raise HTTPException(404,"Link not found")

@router.delete("/by_url/",status_code=status.HTTP_200_OK)
def delete_link_by_url(user: user_dependency, url:str, db:Session = Depends(get_db), redis: Redis = Depends(get_redis)):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')

    user_id = user.get('id')
    assert user_id is not None
    db_link = db.query(database_models.Links).filter(
        database_models.Links.original_url == url).filter(
            database_models.Links.user_id == user_id).first()
    if db_link:
        db.delete(db_link)
        db.commit()

        # Invalidate caches
        redis.delete(links_user(user_id)) #  
        redis.delete(link_key(db_link.short_code)) #  
        return "Link deleted"
    #return "Link not found"
    raise HTTPException(404,"Link not found")

@router.delete("/by_key/",status_code=status.HTTP_200_OK)
def delete_link_by_key(user: user_dependency, key:str, db:Session = Depends(get_db), redis: Redis = Depends(get_redis)):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')

    key = key.replace("http://","").replace("https://","").replace(API_URL,"")
    user_id = user.get('id')
    assert user_id is not None
    db_link = db.query(database_models.Links).filter(
        (database_models.Links.short_code == key)|
        (database_models.Links.alias == key),
        database_models.Links.user_id == user_id).first()
    if db_link:
        db.delete(db_link)
        db.commit()

        # Invalidate caches
        redis.delete(links_user(user_id))
        redis.delete(link_key(db_link.short_code))
        return "Link deleted"
    #return "Link not found"
    raise HTTPException(404,"Link not found")

@router.get("/link/title/",status_code = status.HTTP_200_OK)
async def get_link_title(user: user_dependency, url: str):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')
    
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
        await websocket.close(code=1008, reason="Missing token")
        return

    try:
        user = decode_user_from_token(token)  # already returns username/id/role
    except HTTPException:
        await websocket.close(code=1008, reason="Invalid token")
        return

    user_id = user["id"]
    if not user_id:
        await websocket.close(code=1008, reason="Invalid user id")
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

            else:
                # Unknown message type
                await websocket.send_json({
                    "type": "error",
                    "detail": f"Unknown message type: {mtype}",
                })

    except WebSocketDisconnect:
        # Client ended connection
        return
        

def add_link_to_user(user_id: int, link_id: int, title: str, db: db_dependency):
    user_link = database_models.userLinks(
        user_id=user_id,
        link_id=link_id,
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
    }