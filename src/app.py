"""Photo Butler: runs once per uploaded photo.

1. Download the photo from S3
2. Create a 256px thumbnail
3. Ask Rekognition for labels (Dog, Pizza, Beach...)
4. Save everything to DynamoDB
5. Tell the owner via SNS
"""
import io
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import unquote_plus

import boto3
from PIL import Image, ImageOps

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Created outside the handler so warm invocations reuse the connections
s3 = boto3.client("s3")
rekognition = boto3.client("rekognition")
sns = boto3.client("sns")
table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

THUMBNAIL_BUCKET = os.environ["THUMBNAIL_BUCKET"]
TOPIC_ARN = os.environ["TOPIC_ARN"]
MIN_CONFIDENCE = float(os.environ.get("MIN_CONFIDENCE", "80"))
THUMBNAIL_SIZE = (256, 256)


def lambda_handler(event, context):
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])  # keys arrive URL-encoded
        process_photo(bucket, key)
    return {"processed": len(event["Records"])}


def process_photo(bucket, key):
    logger.info("New photo: s3://%s/%s", bucket, key)

    # 1. Download and create the thumbnail
    original = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    thumb_key = f"{os.path.splitext(key)[0]}_thumb.jpg"
    create_thumbnail(original, thumb_key)
    logger.info("Thumbnail saved: s3://%s/%s", THUMBNAIL_BUCKET, thumb_key)

    # 2. Auto-tag with Rekognition (reads straight from S3, no bytes sent)
    response = rekognition.detect_labels(
        Image={"S3Object": {"Bucket": bucket, "Name": key}},
        MaxLabels=8,
        MinConfidence=MIN_CONFIDENCE,
    )
    labels = [
        {"name": l["Name"], "confidence": Decimal(str(round(l["Confidence"], 1)))}
        for l in response["Labels"]
    ]
    names = [l["name"] for l in labels]
    logger.info("Labels: %s", names)

    # 3. Save to DynamoDB
    table.put_item(
        Item={
            "photoId": key,
            "uploadedAt": datetime.now(timezone.utc).isoformat(),
            "labels": labels,
            "topLabel": names[0] if names else "Unknown",
            "thumbnailKey": thumb_key,
        }
    )

    # 4. Announce it
    tag_text = ", ".join(names[:3]) if names else "no confident tags"
    sns.publish(
        TopicArn=TOPIC_ARN,
        Subject="Photo Butler: new photo",
        Message=f"New photo added: {tag_text}\n\nFile: {key}",
    )


def create_thumbnail(image_bytes, thumb_key):
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)  # respect phone rotation
    img = img.convert("RGB")            # PNG with alpha -> JPEG-safe
    img.thumbnail(THUMBNAIL_SIZE)

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    buffer.seek(0)
    s3.put_object(
        Bucket=THUMBNAIL_BUCKET,
        Key=thumb_key,
        Body=buffer,
        ContentType="image/jpeg",
    )
