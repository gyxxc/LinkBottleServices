from typing import Optional, Annotated
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from utils.database_models import Users
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from utils.database import sessionLocal, engine
from starlette import status
from .auth import get_current_user

router = APIRouter(
    prefix='/user',
    tags=['user']
)

def get_db():
    db = sessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[dict,Depends(get_current_user)]
bcrypt_context = CryptContext(schemes=['bcrypt'],deprecated = 'auto')

class UserVerification(BaseModel):
    password: str
    new_password: str = Field(min_length = 8)


@router.get("/", status_code = status.HTTP_200_OK)
async def get_user(user:user_dependency, db:db_dependency):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')
    return db.query(Users).filter(Users.id == user.get('id')).first()

@router.put("/password",status_code=status.HTTP_202_ACCEPTED)
async def change_password(user:user_dependency, db:db_dependency, 
                          user_verification: UserVerification):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')
    
    user_model = db.query(Users).filter(Users.id == user.get('id')).first()

    if not bcrypt_context.verify(user_verification.password, 
                                 user_model.hashed_password):#type:ignore
        raise HTTPException(401, detail='Old password did not match.')
    user_model.hashed_password = bcrypt_context.hash(user_verification.new_password) #type:ignore
    db.commit()
    return 'Password Changed'

@router.put("/phone",status_code=status.HTTP_202_ACCEPTED)
async def change_phone_number(user:user_dependency, db:db_dependency, 
                              new_number: str):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')
    
    user_model = db.query(Users).filter(Users.id == user.get('id')).first()

    user_model.phone_number = new_number #type:ignore
    db.commit()
    return 'Phone Number Updated'