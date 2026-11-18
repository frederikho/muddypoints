"""Thin helpers around the single DynamoDB table.

Key schema (PK / SK), see CLAUDE.md for the full list:
  USER#<uid>      PROFILE | APIKEY#<key_id>
  EMAIL#<email>   EMAIL
  APIKEY#<sha256> APIKEY
  RESET#<sha256>  RESET                      (ttl)
  SPACE#<sid>     META | MEMBER#<uid> | INVITE#<email>
  BOARD#<bid>     META | SLIDE#<slide_id> | COMMENT#<cid> | LIKE#<target>#<viewer>
GSI1 (GSI1PK / GSI1SK): USER#<uid>->SPACE#<sid> (memberships), SPACE#<sid>->BOARD#<bid>,
                        INVITE#<email>->SPACE#<sid>
"""
from decimal import Decimal

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from . import aws


def clean(value):
    """Convert DynamoDB Decimals to int/float recursively."""
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def is_conditional_failure(err: ClientError) -> bool:
    return err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def get(pk: str, sk: str) -> dict | None:
    item = aws.table().get_item(Key={"PK": pk, "SK": sk}).get("Item")
    return clean(item) if item else None


def put(item: dict, if_absent: bool = False) -> bool:
    """Write an item. With if_absent=True returns False instead of overwriting an existing item."""
    kwargs = {"Item": item}
    if if_absent:
        kwargs["ConditionExpression"] = Attr("PK").not_exists()
    try:
        aws.table().put_item(**kwargs)
        return True
    except ClientError as err:
        if if_absent and is_conditional_failure(err):
            return False
        raise


def delete(pk: str, sk: str, if_exists: bool = False) -> bool:
    kwargs = {"Key": {"PK": pk, "SK": sk}}
    if if_exists:
        kwargs["ConditionExpression"] = Attr("PK").exists()
    try:
        aws.table().delete_item(**kwargs)
        return True
    except ClientError as err:
        if if_exists and is_conditional_failure(err):
            return False
        raise


def update(pk: str, sk: str, fields: dict, must_exist: bool = True) -> dict:
    names, values, sets = {}, {}, []
    for i, (k, v) in enumerate(fields.items()):
        names[f"#f{i}"] = k
        values[f":v{i}"] = v
        sets.append(f"#f{i} = :v{i}")
    kwargs = {
        "Key": {"PK": pk, "SK": sk},
        "UpdateExpression": "SET " + ", ".join(sets),
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
        "ReturnValues": "ALL_NEW",
    }
    if must_exist:
        kwargs["ConditionExpression"] = Attr("PK").exists()
    return clean(aws.table().update_item(**kwargs)["Attributes"])


def increment(pk: str, sk: str, field: str, by: int) -> None:
    try:
        aws.table().update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression="ADD #f :n",
            ExpressionAttributeNames={"#f": field},
            ExpressionAttributeValues={":n": by},
            ConditionExpression=Attr("PK").exists(),
        )
    except ClientError as err:
        if not is_conditional_failure(err):
            raise


def bump_counter(pk: str, sk: str, ttl: int) -> int:
    """Atomically increment a counter item (created if missing) and return the new value."""
    resp = aws.table().update_item(
        Key={"PK": pk, "SK": sk}, UpdateExpression="ADD #n :one SET #t = :ttl",
        ExpressionAttributeNames={"#n": "count", "#t": "ttl"},
        ExpressionAttributeValues={":one": 1, ":ttl": ttl}, ReturnValues="UPDATED_NEW")
    return int(resp["Attributes"]["count"])


def _paginate(**kwargs) -> list[dict]:
    items, start = [], None
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        resp = aws.table().query(**kwargs)
        items.extend(resp.get("Items", []))
        start = resp.get("LastEvaluatedKey")
        if not start:
            return clean(items)


def query(pk: str, sk_prefix: str | None = None) -> list[dict]:
    cond = Key("PK").eq(pk)
    if sk_prefix:
        cond = cond & Key("SK").begins_with(sk_prefix)
    return _paginate(KeyConditionExpression=cond)


def query_gsi1(pk: str, sk_prefix: str | None = None) -> list[dict]:
    cond = Key("GSI1PK").eq(pk)
    if sk_prefix:
        cond = cond & Key("GSI1SK").begins_with(sk_prefix)
    return _paginate(IndexName="GSI1", KeyConditionExpression=cond)


def batch_get(keys: list[tuple[str, str]]) -> list[dict]:
    out: list[dict] = []
    keys = list(dict.fromkeys(keys))
    name = aws.table().name
    for i in range(0, len(keys), 100):
        request = {name: {"Keys": [{"PK": pk, "SK": sk} for pk, sk in keys[i:i + 100]]}}
        while request:
            resp = aws.dynamodb().batch_get_item(RequestItems=request)
            out.extend(resp.get("Responses", {}).get(name, []))
            request = resp.get("UnprocessedKeys") or None
    return clean(out)


def batch_delete(keys: list[tuple[str, str]]) -> None:
    with aws.table().batch_writer() as writer:
        for pk, sk in dict.fromkeys(keys):
            writer.delete_item(Key={"PK": pk, "SK": sk})
