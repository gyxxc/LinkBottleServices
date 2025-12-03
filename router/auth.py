from typing import Optional, Annotated
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from utils import database_models
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from utils.database import sessionLocal, engine
from starlette import status
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from datetime import timedelta,datetime,timezone
from jose import jwt, JWTError

class UserRequest(BaseModel):

    id: Optional[int] = None
    username: str
    email: str
    first_name: str
    last_name: str
    password: str = Field(min_length=8)
    phone_number: Optional[str] = None

    class Config:
        json_schema_extra = {
            'example': {
                'username': 'username',
                'email': 'username@gmail.com',
                'first_name': 'first name',
                'last_name': 'last name',
                'password': 'password',
                'phone_number': '1234567890'
            }
        } 

class Token(BaseModel):
    access_token: str
    token_type: str


bcrypt_context = CryptContext(schemes=['bcrypt'],deprecated = 'auto')
oauth2_bearer = OAuth2PasswordBearer(tokenUrl='auth/token')

SECRET_KEY = 'ef2ad025ea43b13a0047fecd236bb3450a3f3bbf44eecf63cf972f5fe63e8e15'
ALGORITHM = 'HS256'

router = APIRouter(
    prefix='/auth',
    tags=['auth']
)

database_models.Base.metadata.create_all(bind=engine)

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
        db.commit()


init_db()

db_dependency = Annotated[Session, Depends(get_db)]

def authenticate_user(username: str, password: str, db:Session):
    user = db.query(database_models.Users).filter(
        database_models.Users.username == username).first()
    if user:
        if bcrypt_context.verify(password, user.hashed_password): # type: ignore
            return user
    return False

def create_access_token(username: str, user_id: int, role:str, expires_delta: timedelta):
    encode = {'sub':username, 'id':user_id, 'role':role}
    expires = datetime.now(timezone.utc) + expires_delta
    encode.update({'exp':expires})
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

async def get_current_user(token: Annotated[str, Depends(oauth2_bearer)]):
    return decode_user_from_token(token)


@router.get("/")
async def get_user():
    return {'user':'authenticated'}

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_user(db:db_dependency, request: UserRequest):
    if db.query(database_models.Users).filter(
        database_models.Users.username == request.username).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Username already used.')
    if db.query(database_models.Users).filter(
        database_models.Users.username == request.email).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Email already used.')

    user_model = database_models.Users(
        email = request.email,
        first_name = request.first_name,
        last_name = request.last_name,
        username = request.username,
        hashed_password =bcrypt_context.hash(request.password),
        #hashed_password =request.password,
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
    token = create_access_token(user.username,user.id,user.role,timedelta(minutes=20))#type: ignore
    return {'access_token':token,'token_type':'bearer'}