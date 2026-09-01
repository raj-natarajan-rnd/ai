"""DSS ingest: S3 ObjectCreated -> parse JSON -> bulk index into OpenSearch."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import boto3

s3 = boto3.client("s3")
sm = boto3.client("secretsmanager")

ENDPOINT = os.environ["ENDPOINT"]
SECRET = os.environ["SECRET"]
ENV = os.environ.get("ENV_NAME", "poc")
ROOT = os.environ.get("PREFIX_ROOT", "datasets")
DEFAULT_INDEX = os.environ.get("INDEX", f"dss-{ENV}-datasets")
DEFAULT_MAPPING = os.environ.get("MAPPING_KEY", "config/index-mapping.json")

# Bulk requests are split: a whole large file in one request exhausts the heap.
BATCH_DOCS = int(os.environ.get("BATCH_DOCS", "500"))
BATCH_BYTES = int(os.environ.get("BATCH_BYTES", str(5 * 1024 * 1024)))

FALLBACK_MAPPING = b'{"settings":{"number_of_shards":1,"number_of_replicas":0}}'
ACTIONS = {"index", "create", "update", "delete"}

_auth: dict = {}
_ready: set = set()


def auth() -> str:
    """Basic auth header from the master credentials, cached per container."""
    if "h" not in _auth:
        c = json.loads(sm.get_secret_value(SecretId=SECRET)["SecretString"])
        token = f"{c['username']}:{c['password']}".encode()
        _auth["h"] = "Basic " + base64.b64encode(token).decode()
    return _auth["h"]


def request(path: str, method: str, data: bytes | None = None,
            content_type: str = "application/json") -> bytes:
    req = urllib.request.Request(
        ENDPOINT + path, data=data, method=method,
        headers={"Content-Type": content_type, "Authorization": auth()})
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.read()


def target(key: str) -> tuple[str, str]:
    """Map an S3 key to (index, mapping key). A folder under ROOT names the index."""
    parts = key.split("/")
    if len(parts) >= 3 and parts[0] == ROOT and parts[1]:
        group = parts[1].lower()
        return f"dss-{ENV}-{group}", f"config/{group}-mapping.json"
    return DEFAULT_INDEX, DEFAULT_MAPPING


def ensure_index(bucket: str, index: str, mapping_key: str, force: bool = False) -> None:
    """Create the index from its mapping in S3; otherwise _bulk auto-creates it untuned."""
    if index in _ready and not force:
        return
    if force:
        try:
            request("/" + index, "DELETE")
        except urllib.error.HTTPError:
            pass
    else:
        try:
            request("/" + index, "HEAD")
            _ready.add(index)
            return
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
    try:
        body = s3.get_object(Bucket=bucket, Key=mapping_key)["Body"].read()
    except Exception:                                          # noqa: BLE001
        body = FALLBACK_MAPPING
        print(json.dumps({"warn": f"no {mapping_key}; created with defaults",
                          "index": index}))
    request("/" + index, "PUT", body)
    _ready.add(index)
    print(json.dumps({"created_index": index, "from": mapping_key}))


def documents(raw: str):
    """Yield (position, doc) from bulk NDJSON, NDJSON, an array, or a single object."""
    text = raw.strip()
    if not text:
        return
    lines = [l for l in text.splitlines() if l.strip()]

    try:
        first = json.loads(lines[0])
        if isinstance(first, dict) and len(first) == 1 and next(iter(first)) in ACTIONS:
            yield ("BULK", text)
            return
    except json.JSONDecodeError:
        pass

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            for i, doc in enumerate(parsed):
                yield (i, doc)
        else:
            yield (0, parsed)
        return
    except json.JSONDecodeError:
        pass

    for i, line in enumerate(lines):
        yield (i, json.loads(line))


def doc_id(key: str, position) -> str:
    """Stable id from source key and position, so re-ingest upserts."""
    return hashlib.sha256(f"{key}|{position}".encode()).hexdigest()[:20]


class Batcher:
    """Accumulates bulk lines per index and flushes on document count or size."""

    def __init__(self):
        self.lines: dict[str, list[str]] = {}
        self.docs: dict[str, int] = {}
        self.size: dict[str, int] = {}
        self.result = {"docs": 0, "indexed": 0, "failed": 0, "errors": []}

    def add(self, index: str, action: str, doc: str | None) -> None:
        buf = self.lines.setdefault(index, [])
        buf.append(action)
        if doc is not None:
            buf.append(doc)
            self.docs[index] = self.docs.get(index, 0) + 1
            self.result["docs"] += 1
        self.size[index] = self.size.get(index, 0) + len(action) + len(doc or "")
        if self.docs.get(index, 0) >= BATCH_DOCS or self.size[index] >= BATCH_BYTES:
            self.flush(index)

    def flush(self, index: str) -> None:
        buf = self.lines.get(index)
        if not buf:
            return
        body = ("\n".join(buf) + "\n").encode("utf-8")
        self.lines[index] = []
        self.docs[index] = 0
        self.size[index] = 0
        response = json.loads(request(f"/{index}/_bulk", "POST", body,
                                      "application/x-ndjson"))
        items = response.get("items", [])
        bad = [i for i in items if list(i.values())[0].get("error")]
        self.result["indexed"] += len(items) - len(bad)
        self.result["failed"] += len(bad)
        for b in bad[:3]:
            reason = list(b.values())[0]["error"].get("reason")
            if reason not in self.result["errors"]:
                self.result["errors"].append(reason)

    def flush_all(self) -> None:
        for index in list(self.lines):
            self.flush(index)


def handler(event, context=None):
    if event.get("recreate"):
        index = event.get("index", DEFAULT_INDEX)
        mapping = event.get("mapping", DEFAULT_MAPPING)
        ensure_index(event["bucket"], index, mapping, force=True)
        return {"recreated": index}

    batcher = Batcher()
    routed: dict[str, str] = {}

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        index, mapping_key = target(key)
        routed[key] = index
        ensure_index(bucket, index, mapping_key)

        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        for position, doc in documents(raw):
            if position == "BULK":
                pending = None
                for line in doc.splitlines():
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if isinstance(obj, dict) and len(obj) == 1 and next(iter(obj)) in ACTIONS:
                        obj[next(iter(obj))]["_index"] = index
                        pending = json.dumps(obj)
                    elif pending is not None:
                        obj["source_key"] = key
                        batcher.add(index, pending, json.dumps(obj))
                        pending = None
                continue
            if not isinstance(doc, dict):
                continue
            doc["source_key"] = key
            action = json.dumps({"index": {"_index": index, "_id": doc_id(key, position)}})
            batcher.add(index, action, json.dumps(doc))

    batcher.flush_all()
    out = dict(batcher.result, routed=routed)
    print(json.dumps(out))
    return out
