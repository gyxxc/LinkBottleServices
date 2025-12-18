from typing import Optional, Annotated, Dict, Any, cast
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, EmailStr
from utils import database_models
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from utils.database import sessionLocal, engine, Redis, get_redis
from starlette import status
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from datetime import timedelta,datetime,timezone
from jose import jwt, JWTError
from authlib.integrations.starlette_client import OAuth
import os
import random
import hmac
import hashlib
import json
from utils.AWShelper import send_email

class UserRequest(BaseModel):

    username: str = Field(min_length=3, max_length=30,
        pattern=r"^[A-Za-z0-9_-]{3,30}$",
        description="Username of 3–30 chars containing only letters, numbers, _ or -",)
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    password: str = Field(min_length=8)
    phone_number: Optional[str] = None
    otp: str = Field(min_length=6, max_length=6,
        pattern=r"^[0-9]{6}$",
        description="6 digit one time password",)

    class Config:
        json_schema_extra = {
            'example': {
                'username': 'username',
                'email': 'username@gmail.com',
                'first_name': 'first name',
                'last_name': 'last name',
                'password': 'password',
                'phone_number': '1234567890',
                'otp': '123456'
            }
        } 

class CompleteSignupBody(BaseModel):
    pending_token: str
    username: str = Field(min_length=3, max_length=30,
        pattern=r"^[A-Za-z0-9_-]{3,30}$",
        description="Username of 3–30 chars containing only letters, numbers, _ or -",)
    
class BindAccountBody(BaseModel):
    pending_token: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class ChangePasswordRequest(BaseModel):
    old_password: Optional[str] = None
    new_password: str = Field(min_length=8)
    otp: str = Field(min_length=6, max_length=6,
        pattern=r"^[0-9]{6}$",)


bcrypt_context = CryptContext(schemes=['bcrypt'],deprecated = 'auto')
oauth2_bearer = OAuth2PasswordBearer(tokenUrl='auth/token')

SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your-secret-key')
ALGORITHM = 'HS256'
ACCESS_EXPIRE_TIME = 20
PENDING_EXPIRE_TIME = 10
FRONTEND_URL = "http://localhost:3000"
PROVIDER_SECRET_SALT = os.getenv('PROVIDER_SECRET_SALT', 'another-secret-salt')
CODE_TTL_SECONDS = 300  
CODE_RESEND_SECONDS = 60
MAX_ATTEMPTS = 5  

router = APIRouter(
    prefix='/auth',
    tags=['auth']
)

database_models.Base.metadata.create_all(bind=engine)

oauth = OAuth()

oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid email profile",
    },
)

oauth.register(
    name="github",
    client_id=os.getenv("GITHUB_CLIENT_ID"),
    client_secret=os.getenv("GITHUB_CLIENT_SECRET"),
    access_token_url="https://github.com/login/oauth/access_token",
    authorize_url="https://github.com/login/oauth/authorize",
    api_base_url="https://api.github.com/",
    client_kwargs={
        # just enough for login + email
        "scope": "read:user user:email",
    },
)

def get_db():
    db = sessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    db = sessionLocal()
    count = db.query(database_models.Users).count()

    if count == 0:
        user_model = database_models.Users(
            email = 'auaurora@gmail.com',
            first_name = 'Tohya',
            last_name = 'Hachijo',
            username = 'Featherine',
            role = 'admin',
            hashed_password ='$2b$12$SpILDz.0ZlV/1csd0RB4QubBoyLQTLiIMZZF6zuaitnpsa5UrZV/G',
            is_active = True
        )
        db.add(user_model)
        # user_model = database_models.Users(
        #     email = 'angel17@gmail.com',
        #     first_name = 'Ange',
        #     last_name = 'Ushiromiya',
        #     username = 'Angel17',
        #     role = 'user',
        #     hashed_password ='$2b$12$SpILDz.0ZlV/1csd0RB4QubBoyLQTLiIMZZF6zuaitnpsa5UrZV/G',
        #     is_active = True
        # )
        # db.add(user_model)
        db.commit()


init_db()

db_dependency = Annotated[Session, Depends(get_db)]

def generate_numeric_code(length: int = 6) -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(length))

def _load_verification(redis: Redis, key: str) -> Optional[Dict[str, Any]]:
    raw = redis.get(key)
    if not raw:
        return None
    return json.loads(cast(str,raw))

def _increment_attempts(redis: Redis, key: str, verification: Dict[str, Any]) -> None:
    verification["attempts"] += 1
    redis.setex(key, CODE_TTL_SECONDS, json.dumps(verification))

def create_verification_entry(
    redis: Redis,
    key: str,
    code: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "code": bcrypt_context.hash(code),
        "attempts": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload.update(extra)
    redis.setex(key, CODE_TTL_SECONDS, json.dumps(payload))

def verify_otp_code(email, otp, redis: Redis):
    key = f"otp:{email}"
    verification = _load_verification(redis, key)
    if not verification:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No OTP code found. Please request a new code.")
    if verification["attempts"] >= MAX_ATTEMPTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Maximum verification attempts exceeded.")
    if not bcrypt_context.verify(otp, verification["code"]):
        _increment_attempts(redis, key, verification)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid OTP code.")
    redis.delete(key)


def authenticate_user(username: str, password: str, db:Session):
    user = db.query(database_models.Users).filter(
        (database_models.Users.username == username)|
        (database_models.Users.email == username)).first()
    if user:
        if bcrypt_context.verify(password, user.hashed_password): # type: ignore
            return user
    return False

def hash_provider_id(provider: str, provider_id: str) -> str:
    msg = f"{provider}:{provider_id}".encode()
    return hmac.new(key=PROVIDER_SECRET_SALT.encode(), 
                    msg=msg, digestmod=hashlib.sha256).hexdigest()

def create_access_token(username: str, user_id: int, role:str, expires_delta: timedelta):
    encode = {'sub':username, 'id':user_id, 'role':role}
    expires = datetime.now(timezone.utc) + expires_delta
    encode.update({'exp':expires})
    return jwt.encode(encode,SECRET_KEY,algorithm = ALGORITHM)

def create_pending_token(mode: str, provider: str, provider_id:str, email: str | None = None, 
                         expires_delta: timedelta = timedelta(minutes=PENDING_EXPIRE_TIME)):
    expires = datetime.now(timezone.utc) + expires_delta
    encode = {"kind": "oauth_pending",'mode':mode, 'provider':provider, 
              'provider_id':provider_id, 'email':email, 'exp':expires}
    return jwt.encode(encode,SECRET_KEY,algorithm = ALGORITHM)

def decode_user_from_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get('sub')#type: ignore
        user_id: int = payload.get('id')#type: ignore
        role: int = payload.get('role')#type: ignore

        if not username or not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate user."
            )

        return {"username": username, "id": user_id, "role": role}

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate user."
        )

def decode_pending_token(token: str) -> dict:
    data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    if data.get("kind") != "oauth_pending":
        raise ValueError("Not a pending oauth token")
    return data

async def get_current_user(token: Annotated[str, Depends(oauth2_bearer)]):
    return decode_user_from_token(token)

user_dependency = Annotated[dict, Depends(get_current_user)]

@router.get("/google/login")
async def google_login(request: Request):
    google = oauth.create_client("google")
    redirect_uri = request.url_for("google_callback")
    return await google.authorize_redirect(request, redirect_uri) #type:ignore

@router.get("/github/login")
async def github_login(request: Request):
    github = oauth.create_client("github")
    redirect_uri = request.url_for("github_callback")
    return await github.authorize_redirect(request, redirect_uri)#type:ignore

@router.get("/google/callback")
async def google_callback(request: Request, db=Depends(get_db)):
    google = oauth.create_client("google")
    token = await google.authorize_access_token(request) #type:ignore
    userinfo = token.get("userinfo")  

    if not userinfo:
        resp = await google.get("userinfo", token=token) #type:ignore
        userinfo = resp.json()

    # Extract  identifiers
    email = userinfo["email"]
    sub = hash_provider_id("google",userinfo["sub"])  
    username = userinfo.get("name", "")
    return RedirectResponse(oauth_login(db, "google", sub, username, email), status_code=302)

@router.get("/github/callback")
async def github_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    github = oauth.create_client("github")

    token = await github.authorize_access_token(request)#type:ignore

    userinfo = token.get("userinfo")

    if not userinfo:
        resp = await github.get("user", token=token)#type:ignore
        userinfo = resp.json()
    
    # Extract  identifiers
    provider_id = hash_provider_id("github", str(userinfo["id"]))
    username = userinfo.get("login", "")
    email = userinfo.get("email")
    if not email:
        resp_emails = await github.get("user/emails", token=token)  #type:ignore
        emails = resp_emails.json()
        primary = next(
            (e["email"] for e in emails if e.get("primary") and e.get("verified")),
            None,
        )
        email = primary or (emails[0]["email"] if emails else None)

    return RedirectResponse(oauth_login(db, "github", provider_id, username, email), status_code=302)

def oauth_login(db: Session, provider: str, provider_id: str,  username: str, email: str | None = None):
    # Find or create local user
    oauth_user = get_oauth_link(db, provider, provider_id)
    if oauth_user:
        access_token = create_access_token(oauth_user.username,oauth_user.id,
                                           oauth_user.role,timedelta(minutes=ACCESS_EXPIRE_TIME))

        params = urlencode({
            "status": "logged_in",
            "access_token": access_token,
            "token_type": "bearer",
        })

        frontend = f"{FRONTEND_URL}/oauth/{provider}?{params}"
        return frontend
    
    if email:
        existing_user = db.query(database_models.Users).filter(database_models.Users.email == email).first()
        if existing_user:
            pending_token = create_pending_token("link", provider, provider_id, email)
            params = urlencode({
                "status": "link_existing",
                "email": email,
                "pending_token": pending_token,
            })
            frontend = f"{FRONTEND_URL}/oauth/{provider}?{params}"
            return frontend
    
    pending_token = create_pending_token("signup", provider, provider_id, email)
    
    params = urlencode({
        "status": "new_user",
        "pending_token": pending_token,
        "suggested_username": username,
        "email": email or "",
    })
    frontend = f"{FRONTEND_URL}/oauth/{provider}?{params}"
    return frontend

def get_oauth_link(db: Session, provider: str, provider_id: str):
    if provider == "google":
        return db.query(database_models.Users).filter(
            database_models.Users.google_sub == provider_id).first()
    elif provider == "github":
        return db.query(database_models.Users).filter(
            database_models.Users.github_id == provider_id).first()
    return None

@router.post("/complete-signup", response_model = Token)
async def complete_signup(
    body: CompleteSignupBody,
    db: Session = Depends(get_db),
):
    try:
        data = decode_pending_token(body.pending_token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token")

    if data.get("mode")!= "signup":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")

    provider = data["provider"]
    provider_id = data["provider_id"]
    email = data.get("email")

    # Check username availability
    user_check = db.query(database_models.Users).filter(database_models.Users.username == body.username).first()
    if user_check:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Username already taken.')

    user_model = database_models.Users(
        email = email,
        username = body.username,
        google_sub = provider_id if provider == "google" else None,
        github_id = provider_id if provider == "github" else None,
        role = 'user',
        is_active = True,
    )

    db.add(user_model)
    db.commit()
    db.refresh(user_model)

    # Issue normal access token
    expires_delta = timedelta(minutes=ACCESS_EXPIRE_TIME)
    access_token = create_access_token(
        username=user_model.username,
        user_id=user_model.id,
        role=user_model.role,
        expires_delta=expires_delta,
    )

    return {
        "access_token": access_token,
        "token_type": "bearer"
    }

@router.post("/bind-account", response_model = Token)
async def bind_account(
    body: BindAccountBody,
    db: Session = Depends(get_db),
):
    try:
        data = decode_pending_token(body.pending_token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token")

    if data.get("mode") != "link":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Wrong pending mode")

    provider = data["provider"]
    provider_id = data["provider_id"]
    email = data.get("email")

    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email missing")

    # 1. Find local user by email
    user = db.query(database_models.Users).filter(database_models.Users.email == email).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # 2. Verify password
    if not authenticate_user(user.username, body.password, db):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect password")

    # 3. Ensure we haven't already linked this provider to someone else (paranoia)
    existing_link = get_oauth_link(db, provider=provider, provider_id=provider_id)
    if existing_link:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This social account is already linked")

    # 4. Create link between this user and the provider
    if provider == "google":
        user.google_sub = provider_id
    elif provider == "github":
        user.github_id = provider_id
    db.commit()

    # 5. Issue normal access token and log them in
    access_token = create_access_token(
        username=user.username,
        user_id=user.id,
        role=user.role,
        expires_delta=timedelta(minutes=ACCESS_EXPIRE_TIME),
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
    }

@router.get("/otp/get-code/")
async def get_otp_code(redis: Annotated[Redis, Depends(get_redis)], email: EmailStr):
    key = f"otp:{email}"
    existing = _load_verification(redis, key)
    if existing:
        created_at = datetime.fromisoformat(existing["created_at"])
        age = (datetime.now(timezone.utc) - created_at).total_seconds()
        if age < CODE_RESEND_SECONDS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="A code has already been sent recently.")
    
    code = generate_numeric_code(6)
    create_verification_entry(redis, key, code)

    #This requires AWS SES setup
    send_email(
        to=email,
        subject="Your LinkBottle account's One Time Passcode",
        body=f"Your One Time Passcode is: \n {code}",
    )
    
    # Right now, also just return the code to frontend.
    return {"detail": "OTP code generated and sent.", "code": code}

@router.post("/create_user/", status_code=status.HTTP_201_CREATED)
def create_user(db:db_dependency, request: UserRequest, redis: Annotated[Redis, Depends(get_redis)]):
    verify_otp_code(request.email, request.otp, redis)
    
    user_check = db.query(database_models.Users).filter(database_models.Users.username == request.username).first()
    if user_check:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Username already taken.')
    user_check = db.query(database_models.Users).filter(database_models.Users.email == request.email).first()
    if user_check:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='E-mail already taken.')

    user_model = database_models.Users(
        email = request.email,
        first_name = request.first_name,
        last_name = request.last_name,
        username = request.username,
        hashed_password =bcrypt_context.hash(request.password),
        role = 'user',
        is_active = True,
        phone_number = request.phone_number
    )
    
    db.add(user_model)
    db.commit()
    return "User Created"

@router.post("/token", response_model = Token)
async def login_for_access_token(formdata: Annotated[OAuth2PasswordRequestForm, Depends()],
                                 db: db_dependency):
    user = authenticate_user(formdata.username, formdata.password, db)
    if not user: 
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail='Could not validate user.')
    token = create_access_token(user.username,user.id,user.role,timedelta(minutes=ACCESS_EXPIRE_TIME))#type: ignore
    return {'access_token':token,'token_type':'bearer'}

@router.post("/change-password", status_code=status.HTTP_202_ACCEPTED)
def change_password(user:user_dependency, db:db_dependency, redis: Annotated[Redis, Depends(get_redis)], 
                   request: ChangePasswordRequest):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')

    user_model = db.query(database_models.Users).filter(database_models.Users.id == user.get('id')).first()
    if not user_model:
        raise HTTPException(401, detail='Authentication Failed.')

    verify_otp_code(user_model.email, request.otp, redis)

    if user_model.hashed_password:
        if not request.old_password:
            raise HTTPException(400, detail='Old password is required.')
        if not bcrypt_context.verify(request.old_password, user_model.hashed_password):
            raise HTTPException(401, detail='Old password did not match.')
    user_model.hashed_password = bcrypt_context.hash(request.new_password)
    db.commit()
    return 'Password Changed'

@router.post("/forget-password", status_code=status.HTTP_202_ACCEPTED)
def forget_password(request: ChangePasswordRequest, redis: Annotated[Redis, Depends(get_redis)],
                    db: db_dependency, email: EmailStr):
    user_model = db.query(database_models.Users).filter(
        database_models.Users.email == email).first()
    if not user_model:
        raise HTTPException(404, detail='User not found.')

    verify_otp_code(user_model.email, request.otp, redis)

    user_model.hashed_password = bcrypt_context.hash(request.new_password)
    db.commit()
    return 'Password Changed'