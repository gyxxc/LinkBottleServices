# LinkBottleServices

LinkBottle is a powerful online resource shortening service with support for custom aliases, QR/barcode generation, and click analytics.

## How to Run

All the python dependencies are in `requirements.txt`, which can be installed using the following command:

```cmd
pip install -r requirements.txt
```

The following environment variables are required to run the API:

```py
DATABASE_URL: "postgresql+psycopg://USER:PASSWORD@HOST:PORT/DBNAME" #URL to postgres database
REDIS_URL: "redis://USER:PASSWORD@HOST:PORT/0" #URL to redis cache

JWT_SECRET_KEY: "" #JWT Token for user authentication
GOOGLE_CLIENT_ID: "CLIENTID.apps.googleusercontent.com" #google API client for OAuth sign-in
GOOGLE_CLIENT_SECRET: "" #google API client Secret for OAuth sign-in
GITHUB_CLIENT_ID: "" #GitHub API client for OAuth sign-in
GITHUB_CLIENT_SECRET: "" #GitHub API client Secret for OAuth sign-in
MIDDLEWARE_SECRET: "" #Middleware Secret to connect to Google/Github APIs
PROVIDER_SECRET_SALT: "" #Secret for encoding provider ids from Google and GitHub Oauth sign-in

SAFE_BROWSING_API_KEY: "" #Implements spam/malicious link detection through Google API
OPENAI_API_KEY: "" #Implements spam/malicious link detection through OpenAI API

AWS_ACCESS_KEY: "" #AWS services for qr code storage / email sending
AWS_SECRET_KEY: "" #AWS services for qr code storage / email sending
AWS_REGION: "" #AWS services for qr code storage / email sending
AWS_BUCKET_NAME: "" #AWS S3 bucket to store the qr code
SES_FROM_EMAIL: "" #The email address used by AWS SES
```

After fulfilling the above requirements, the app can be started by

```cmd
uvicorn main:app --reload
```

or simply

```cmd
python run.py
```

## Click_worker.py

This is a background worker to be run separately in Docker. It flushes clicks cached in Redis to the database.

## How to deploy in Docker

run docker compose with the following yaml:

```yaml

services:
  redis:
    image: redis:7-alpine
    volumes:
      - ./redis.conf:/usr/local/etc/redis/redis.conf
    command: ["redis-server", "/usr/local/etc/redis/redis.conf"]
    restart: always
    ports:
      - "6379:6379"
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: ""
      POSTGRES_PASSWORD: ""
      POSTGRES_DB: ""
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5433:5432"

  app:
    build: .
    command: uvicorn main:app --host 0.0.0.0 --port 8000
    environment:
      DATABASE_URL: ""
      REDIS_URL: ""
      GOOGLE_CLIENT_ID: ""
      GOOGLE_CLIENT_SECRET: ""
      JWT_SECRET_KEY: ""
      MIDDLEWARE_SECRET: ""
      GITHUB_CLIENT_ID: ""
      GITHUB_CLIENT_SECRET: ""
      SAFE_BROWSING_API_KEY: ""
      OPENAI_API_KEY: ""
      PROVIDER_SECRET_SALT: ""
      AWS_ACCESS_KEY: ""
      AWS_SECRET_KEY: ""
      AWS_REGION: ""
      AWS_BUCKET_NAME: ""
      SES_FROM_EMAIL: ""

    depends_on:
      - db
      - redis
    ports:
      - "8000:8000"
  worker:
    build: .
    # same image as api
    # or: image: myapp:latest
    command: python click_worker.py
    depends_on:
      - db
      - redis
    environment:
      DATABASE_URL: ""
      REDIS_URL: ""

volumes:
  pgdata:

```

Fill out the environment variables accordingly. You may also change the ports used.
You also need a redis.conf file to set up Redis.
