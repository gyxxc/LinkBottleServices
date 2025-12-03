from fastapi.middleware.cors import CORSMiddleware
#------------------------------------
#python throws error if I don't inline models.py
from fastapi import FastAPI
from utils import database_models
from utils.database import engine
from router import auth, links, admin, users

app = FastAPI()
app.add_middleware(
    CORSMiddleware, 
    allow_origins = ["*"],
    allow_methods = ["*"],
    allow_headers = ["*"],
    allow_credentials = True
    )

database_models.Base.metadata.create_all(bind=engine)

app.include_router(auth.router)
app.include_router(links.router)
app.include_router(admin.router)
app.include_router(users.router)

@app.get("/")
def greet():
    return 'Welcome to Linkbottle API'