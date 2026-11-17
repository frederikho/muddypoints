"""Create the DynamoDB table and S3 bucket against moto (tests / local dev server).

Must mirror template.yaml (key schema, GSI1, TTL).
"""
from . import aws
from .config import settings


def create_resources() -> None:
    aws.dynamodb().create_table(
        TableName=settings.table_name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("PK", "SK", "GSI1PK", "GSI1SK")],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        GlobalSecondaryIndexes=[{
            "IndexName": "GSI1",
            "KeySchema": [{"AttributeName": "GSI1PK", "KeyType": "HASH"}, {"AttributeName": "GSI1SK", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )
    s3 = aws.s3()
    if settings.region == "us-east-1":
        s3.create_bucket(Bucket=settings.bucket_name)
    else:
        s3.create_bucket(Bucket=settings.bucket_name,
                         CreateBucketConfiguration={"LocationConstraint": settings.region})
    s3.put_bucket_cors(Bucket=settings.bucket_name, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["*"], "AllowedMethods": ["GET", "PUT"], "AllowedHeaders": ["*"]}]})
