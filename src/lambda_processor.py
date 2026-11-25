import io
import json
import os
import uuid
from datetime import datetime, timezone
import boto3
from PIL import Image, ImageDraw

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")

SOURCE_BUCKET = os.environ.get("SOURCE_BUCKET", "img-pipeline-raw-uploads")
DEST_BUCKET = os.environ.get("DEST_BUCKET", "img-pipeline-processed")
TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "ImageMetadata")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")


def get_upload_url(event, context):
    body = json.loads(event.get("body") or "{}")
    filename = body.get("fileName", f"{uuid.uuid4()}.jpg")
    content_type = body.get("contentType", "image/jpeg")
    object_key = f"uploads/{filename}"

    url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": SOURCE_BUCKET, "Key": object_key, "ContentType": content_type},
        ExpiresIn=300,
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"uploadUrl": url, "key": object_key}),
    }


def process_image(event, context):
    bucket = event["bucket"]
    key = event["key"]
    image_id = str(uuid.uuid4())

    raw_obj = s3.get_object(Bucket=bucket, Key=key)
    img = Image.open(io.BytesIO(raw_obj["Body"].read())).convert("RGB")
    width, height = img.size

    web_img = img.copy()
    web_img.thumbnail((1280, 1280))

    thumb_img = img.copy()
    thumb_img.thumbnail((300, 300))

    draw = ImageDraw.Draw(web_img)
    draw.text((24, 24), "Processed on AWS", fill=(255, 255, 255))

    base_name = os.path.basename(key)
    web_key = f"web/{image_id}_{base_name}"
    thumb_key = f"thumbs/{image_id}_{base_name}"

    save_to_s3(web_img, web_key)
    save_to_s3(thumb_img, thumb_key)

    item = {
        "ImageId": image_id,
        "SourceKey": key,
        "WebKey": web_key,
        "ThumbnailKey": thumb_key,
        "Dimensions": f"{width}x{height}",
        "Status": "DONE",
        "UpdatedAt": datetime.now(timezone.utc).isoformat(),
    }

    dynamodb.Table(TABLE_NAME).put_item(Item=item)

    if SNS_TOPIC_ARN:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject="Image Ready",
            Message=json.dumps(item),
        )

    return item


def save_to_s3(image_obj, key):
    buf = io.BytesIO()
    image_obj.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    s3.put_object(Bucket=DEST_BUCKET, Key=key, Body=buf, ContentType="image/jpeg")
