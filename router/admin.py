from typing import Optional, Annotated
from fastapi import APIRouter, Depends, Path, Query, HTTPException
from sqlalchemy.exc import IntegrityError
from utils.database import sessionLocal, engine
from starlette import status
from utils import database_models
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from .auth import get_current_user
from .links import API_URL, fetch_title

from pydantic import BaseModel, Field, HttpUrl
class Link(BaseModel):
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

router = APIRouter(
    prefix='/admin',
    tags = ['admin']
)

def get_db():
    db = sessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[dict,Depends(get_current_user)]

@router.get("/users")
def get_all_users(user: user_dependency, db: db_dependency):
    if user is None or user.get('role')!='admin':
        raise HTTPException(401, detail='Authentication Failed.')
    
    db_users = db.query(database_models.Users).all()
    return db_users


@router.put("/users/{id}",status_code = status.HTTP_202_ACCEPTED)
def change_user_role(user: user_dependency, db: db_dependency, id: int, new_role:str):
    if user is None or user.get('role')!='admin':
        raise HTTPException(401, detail='Authentication Failed.')
    
    db_user = db.query(database_models.Users).filter(
        database_models.Users.id == id).first()
    
    if db_user:
        if db_user.id == 1: # type: ignore
            raise HTTPException(status.HTTP_403_FORBIDDEN,"Cannot modify this user")
        db_user.role = new_role #type: ignore
        db.commit()
        return "User Updated"
    raise HTTPException(404,"User not found")

@router.delete("/users/",status_code = status.HTTP_202_ACCEPTED)
def delete_user(user: user_dependency, db: db_dependency, username: str):
    if user is None or user.get('role')!='admin':
        raise HTTPException(401, detail='Authentication Failed.')
    
    db_user = db.query(database_models.Users).filter(
        database_models.Users.username == username).first()
    
    if not db_user: raise HTTPException(404,"User not found")

    if db_user.id == 1: # type: ignore
        raise HTTPException(status.HTTP_403_FORBIDDEN,"Cannot delete this user")
    
    db.delete(db_user)
    db.commit()
    return "User Deleted"
    
@router.get("/links")
def get_all_links(user: user_dependency, db: db_dependency):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')
    
    db_links = db.query(database_models.Links).all()
    return db_links

@router.get("/links/")
def get_link_by_name(user: user_dependency, db: db_dependency, name:str):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')

    db_link = db.query(database_models.Links).filter(
        func.lower(database_models.Links.name) == name.casefold()).all()
    if db_link: return db_link
    raise HTTPException(404,"Link not found")

@router.get("/links/")
def get_link_by_key(user: user_dependency, db: db_dependency, key:str):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')

    db_link = db.query(database_models.Links).filter(
        (database_models.Links.short_code == key) |
        (database_models.Links.short_url == key)).first()
    if db_link: return db_link
    #return key+": Link not found"
    raise HTTPException(404,key+": Link not found")

@router.put("/links/",status_code = status.HTTP_202_ACCEPTED)
def update_link(user: user_dependency, db: db_dependency, 
                   link:Link, key:str):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')
    
    db_link = db.query(database_models.Links).filter(
        (database_models.Links.short_code == key) |
        (database_models.Links.short_url == key)).first()
    if db_link:
        link_check = db.query(database_models.Links).filter(
            (database_models.Links.alias == link.alias)|(database_models.Links.short_code == link.alias)
            |(database_models.Links.original_url == str(link.original_url)), 
            database_models.Links.id != db_link.id
            ).first()
        if link_check:
            raise HTTPException(409,detail="Another link with same alias or URL already exists.")

        db_link.title = link.title # type: ignore
        db_link.alias = link.alias # type: ignore
        db_link.original_url = str(link.original_url) # type: ignore
        db.commit()
        return "Link Updated"
    #return "Link not found"
    raise HTTPException(404,"Link not found")

@router.delete("/{key}",status_code=status.HTTP_200_OK)
def delete_link_by_key(user: user_dependency, key:str, db:Session = Depends(get_db)):
    if not user:
        raise HTTPException(401, detail='Authentication Failed.')

    db_link = db.query(database_models.Links).filter(
        (database_models.Links.short_code == key) |
        (database_models.Links.short_url == key)).first()
    if db_link:
        db.delete(db_link)
        db.commit()
        return "Link deleted"
    #return "Link not found"
    raise HTTPException(404,"Link not found")