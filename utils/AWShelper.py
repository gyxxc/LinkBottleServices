from io import BytesIO
import qrcode
import boto3
import os

AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
AWS_BUCKET_NAME = os.getenv("AWS_BUCKET_NAME", "linkbottle-bucket")
SES_FROM_EMAIL= os.getenv("SES_FROM_EMAIL")

s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
)

def generate_qr_code(data: str) -> BytesIO:
    """
    Generate a QR code for the given data and return it as a BytesIO object.
    """
    qr = qrcode.QRCode(#type: ignore
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L, #type: ignore
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    byte_io = BytesIO()
    img.save(byte_io, format="PNG") #type: ignore
    byte_io.seek(0)
    return byte_io

def upload_qr_to_s3(key: str, png_bytes: bytes) -> str:
    s3_key = f"qr/{key}.png"

    s3.put_object(
        Bucket=AWS_BUCKET_NAME,
        Key=s3_key,
        Body=png_bytes,
        ContentType="image/png",
    )

    # Return the public URL
    return f"https://{AWS_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"

ses = boto3.client(
    "ses",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
)

def send_email(to: str, subject: str, body: str):
    ses.send_email(
        Source=SES_FROM_EMAIL,
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": body}},
        },
    )