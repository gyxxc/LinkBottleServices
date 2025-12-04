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


---

### 2. `GET /{key}` — Redirect to original URL (ShortToLong)

**Auth required**: No  

**Response**

- **302 redirect** to the full original URL.

---

### 3. `GET /links` — List current user’s links

Returns all links associated with the authenticated user.

**Auth required**: Yes  

**Response 200**

```py
[
  {
    "user_link_id": 10,
    "id": 1,
    "short_code": "a1b2c3",
    "alias": "my-custom-alias",
    "short_url": "localhost:8000/a1b2c3",
    "original_url": "https://example.com",
    "title": "Custom title for me",
    "default_title": "Fetched page title",
    "tags": [],
    "clicks": 12,
    "created_at": "2025-11-25T18:00:00+00:00"
  }
]
```
---

### 4. `DELETE /by_url/` — Delete link by original URL

Deletes a link belonging to the user using with matching URL. Currently unused.

**Auth required**: Yes  

**Query param:** `url`

**Response 200**

```py
"Link deleted"
```

---

### 5. `DELETE /by_key/` — Delete link by short_code or alias

Deletes a link belonging to the user using with matching short code or alias.

**Auth required**: Yes  

**Query param:** `key`

**Response 200**

```py
"Link deleted"
```

---

### 6. `GET /link/title/` — Fetch page title for a URL

Web crawler using BeautifulSoup to automatically fetch the title after inputting the URL. Also sets the default title of the Link in the database. Returns `Failed to Fetch Title` if the website rejects.

**Auth required**: Yes  

**Query param:** `url`

**Response 200**  
Plain text:

Examples:

- `"My Page Title"`
- `"Failed to Fetch Title"`
- `"No Title"`

---

### 7. `GET /links/qrcode/` — Get QR code for a short link

Generates a QR code for a given Link using its short code or alias

**Auth required**: Yes  

**Query param:** `key`

**Response 200**

- PNG image (`image/png`)

---

# WebSocket API

### `WS /ws/batch-upload/` — Batch link upload

Opens a WebSocket for batch uploading of links.

**Auth required**: Yes

---

### Client → Server messages

---

#### 1. `start`

```py
{
  "type": "start",
  "total": 3
}
```

**Server response**

```py
{
  "type": "started",
  "total": 3
}
```

---

#### 2. `item`

```py
{
  "type": "item",
  "data": {
    "alias": "batch-1",
    "title": "Batch link 1",
    "original_url": "https://example.com/1"
  }
}
```

**Possible server responses**

Successful item:

```py
{
  "type": "item_result",
  "index": 1,
  "status": "ok",
  "id": 10,
  "short_url": "localhost:8000/batch-1",
  "short_code": null,
  "alias": "batch-1"
}
```

Business logic error:

```py
{
  "type": "item_result",
  "index": 1,
  "status": "error",
  "code": 409,
  "detail": "You already have a link for this URL."
}
```

Unexpected error:

```py
{
  "type": "item_result",
  "index": 1,
  "status": "error",
  "code": 500,
  "detail": "Some error"
}
```

Optional progress message:

```py
{
  "type": "progress",
  "processed": 1,
  "total": 3
}
```

---

#### 3. `finish`

```py
{
  "type": "finish"
}
```

**Server response**

```py
{
  "type": "finished",
  "processed": 3,
  "total": 3
}
```

---

#### 4. Unknown message types

```py
{
  "type": "error",
  "detail": "Unknown message type: <value>"
}
```

---

# End of API Document
