# Links API

This document describes the 2 HTTP APIs implemented in `links.py` for

- Creating a shortened link.
- Redirecting to the original url using the shortened link.

As well as the Database Schema for to implement the APIs.

## Base URL

Assuming the FastAPI app is running locally:

- **Base URL**: `http://localhost:8000`

> Note: The router in this file does not define a prefix, so the paths below are relative to the app root.

---

## Authentication

Most endpoints require an authenticated user (via `get_current_user`). That means:

- **HTTP endpoints** (except `GET /{key}`) expect an `Authorization` header:

```py
Authorization: Bearer <access_token>
```

- **WebSocket endpoint** expects *either*:
  - A `token` query parameter: `ws://localhost:8000/ws/batch-upload/?token=<access_token>`, **or**
  - An `Authorization` header: `Authorization: Bearer <access_token>`

If the token is missing or invalid, the server responds with `401 Unauthorized` (HTTP) or closes the WebSocket with code `1008`.

---

## Data Model

### `Users` Database

Stores the information of users.

```py
class Users(Base):

    __tablename__ = "users"

    id =  mapped_column(Integer, primary_key=True, index=True)
    email =  mapped_column(String, unique= True)
    username =  mapped_column(String, unique= True)
    first_name =  mapped_column(String)
    last_name =  mapped_column(String)
    hashed_password =  mapped_column(String)
    is_active =  mapped_column(Boolean, default=True)
    role =  mapped_column(String)
    phone_number =  mapped_column(String, nullable=True)
```

### `Links` Database

Stores the information of created links. When a shortened link is created, it either has an automatically generated `short_code` or an `alias` set by the user. The differentiation between `short_code` and `alias` is necessary: When a different user tries to create a short link for an existing original url and without using a customized `alias`, they are simply given the link that has the `short_code`.

```py
class Links(Base):

    __tablename__ = "links"

    id =  mapped_column(Integer, primary_key=True, index=True)
    short_code =  mapped_column(String, nullable=True , unique=True, index=True)
    alias =  mapped_column(String, nullable=True, unique=True)
    title =  mapped_column(String)
    original_url =  mapped_column(String, nullable=False)
    short_url =  mapped_column(String, unique=True, nullable=False)
    created_at =  mapped_column(TIMESTAMP)
    clicks =  mapped_column(Integer, default=0)
    user_id =  mapped_column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True, index=True)
```

### `userLinks` Database

Stores user customizations of the Links, which identifies the links added to a user's Link list, as well as their customized titles and tags.

```py
class userLinks(Base):

    __tablename__ = "user_links"

    id =  mapped_column(Integer, primary_key=True, index=True)
    user_id =  mapped_column(Integer, ForeignKey('users.id', ondelete="CASCADE"), nullable=False, index=True)
    link_id =  mapped_column(Integer, ForeignKey('links.id', ondelete="CASCADE"), nullable=False, index=True)
    title =  mapped_column(String)
    tags =  mapped_column(ARRAY(String))

    _unique_constraint_ = ('user_id', 'link_id')
```

### `LinkRequest` (request body schema)

Used for creating and updating links.

| Field        | Type     | Required | Description                                                                                          |
|-------------|----------|----------|------------------------------------------------------------------------------------------------------|
| `alias`     | string   | No       | Optional custom alias. 3–30 chars, only letters, digits, `_` or `-`.                                |
| `title`     | string   | No       | Optional human-readable title for the link.                                                         |
| `original_url` | URL   | **Yes**  | Original target URL. Must be a valid HTTP/HTTPS URL.                                                |

Example:

```py
{
  "alias": "my-custom-alias",
  "title": "My Example Link",
  "original_url": "https://example.com/resource"
}
```



---

## Endpoints

---

### 1. `POST /shorten/` — Create a new short link (LongToShort)

Creates a new shortened URL for the authenticated user, with optional custom alias.

**Auth required**: Yes  

**Request body (JSON)**

```py
{
  "alias": "my-custom-alias",
  "title": "My Example Link",
  "original_url": "https://example.com"
}
```

**Response 201**

```py
{
  "id": 1,
  "alias": "my-custom-alias",
  "original_url": "https://example.com",
  "user_id": 42,
  "title": "Fetched page title",
  "short_code": null,
  "short_url": "localhost:8000/my-custom-alias",
  "clicks": 0,
  "created_at": "2025-11-25T18:00:00+00:00"
}
```

**Code**

```py
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
```

The `create_short_link_for_user` function is refractored for use in the WebSocket.

---

### 2. `GET /{key}` — Redirect to original URL (ShortToLong)

**Auth required**: No  

**Response**

- **302 redirect** to the full original URL.

**Code**

```py
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
```

---

# End of API Document
