#!/usr/bin/env python3
"""Pinned OpenAPI lookup and direct HTTP/HTTPS requests to Bambuddy."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import http.client
import json
import mimetypes
import os
from pathlib import Path
import re
import stat
import sys
import ssl
import tempfile
import time
from urllib.parse import quote, urlencode, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "references/api"
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
MAX_BYTES = 128 * 1024 * 1024
SECRET = re.compile(
    r"password|secret|token|access.?code|serial.?number|api.?key|authorization|cookie",
    re.I,
)
WRITE_GETS = {"/api/v1/printers/{printer_id}/camera/stop", "/api/v1/settings/backup"}
CAMERA_TOKEN_CACHE_TTL = 59 * 60  # The documented stream-token lifetime is 60 minutes.


class APIError(Exception):
    pass


def redact(value):
    if isinstance(value, dict):
        return {
            key: "********"
            if SECRET.search(key) and item is not None and not isinstance(item, bool)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def operations(spec):
    return [
        {"method": method.upper(), "path": path, "operation": operation}
        for path, item in spec["paths"].items()
        for method, operation in item.items()
        if method in METHODS
    ]


def selector(item):
    return f"{item['method']} {item['path']}"


def short_name(item):
    return item["operation"].get("operationId", "").split("_api_", 1)[0]


def select_operation(spec, name):
    items = operations(spec)
    exact = [item for item in items if selector(item) == name]
    if exact:
        return exact[0]
    matches = [
        item
        for item in items
        if name in (item["operation"].get("operationId"), short_name(item))
    ]
    # FastAPI publishes equivalent slash/no-slash aliases with different IDs.
    routes = {(item["method"], item["path"].rstrip("/")) for item in matches}
    if len(routes) == 1:
        return min(matches, key=lambda item: len(item["path"]))
    if not matches:
        raise APIError(f"Unknown operation: {name}. Use operations SEARCH.")
    raise APIError(
        "Ambiguous operation; use an exact method/path selector:\n"
        + "\n".join(selector(item) for item in matches)
    )


def refs_in(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for item in value.values():
            yield from refs_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from refs_in(item)


def resolve_ref(spec, ref):
    if not ref.startswith("#/"):
        raise APIError(f"External schema reference is unsupported: {ref}")
    value = spec
    for part in ref[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def with_schemas(spec, value):
    found, pending = {}, list(refs_in(value))
    while pending:
        ref = pending.pop()
        if ref in found:
            continue
        found[ref] = resolve_ref(spec, ref)
        pending.extend(refs_in(found[ref]))
    return {"definition": value, "referenced_schemas": found}


def connection(base_url=None):
    base_url = base_url if base_url is not None else os.environ.get("BAMBUDDY_URL", "")
    if not base_url.strip():
        raise APIError("Set BAMBUDDY_URL or pass --url for the Bambuddy instance")
    base_url = base_url.strip().rstrip("/")
    try:
        url = urlsplit(base_url)
        valid = (
            url.scheme in ("http", "https")
            and url.hostname
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and (url.port is None or 1 <= url.port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        raise APIError(
            "Bambuddy URL must be HTTP/HTTPS without embedded credentials, query or fragment"
        )
    key = os.environ.get("BAMBUDDY_API_KEY", "")
    if not key.strip():
        raise APIError("Set BAMBUDDY_API_KEY for authenticated API requests")
    return {"base_url": base_url, "api_key": key}


def exchange(config, method, path, body=b"", content_type=None, timeout=30):
    if len(body) > MAX_BYTES:
        raise APIError("Request exceeds 128 MiB limit")
    url = urlsplit(config["base_url"])
    headers = {"Accept": "application/json, */*"}
    if content_type:
        headers["Content-Type"] = content_type
    headers["X-API-Key"] = config["api_key"]
    if url.scheme == "https":
        context = ssl.create_default_context()
        client = http.client.HTTPSConnection(
            url.hostname, url.port or 443, timeout=timeout, context=context
        )
    elif url.scheme == "http":
        client = http.client.HTTPConnection(
            url.hostname, url.port or 80, timeout=timeout
        )
    else:
        raise APIError("Only direct HTTP/HTTPS API requests are supported")
    try:
        client.request(method, url.path.rstrip("/") + path, body=body, headers=headers)
        response = client.getresponse()
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise APIError("Response exceeds 128 MiB limit")
        return {
            "status": response.status,
            "content_type": response.getheader("Content-Type", ""),
        }, raw
    except ssl.SSLCertVerificationError as exc:
        raise APIError("TLS certificate verification failed") from exc
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise APIError(
            f"HTTP request failed ({type(exc).__name__}); inspect state before retrying a write"
        ) from exc
    finally:
        client.close()


def pairs(values):
    result = []
    for value in values:
        if "=" not in value:
            raise APIError("Expected NAME=VALUE")
        key, item = value.split("=", 1)
        result.append((key, item))
    return result


def request_path(spec, item, path_values, query_values):
    params = spec["paths"][item["path"]].get("parameters", []) + item["operation"].get(
        "parameters", []
    )
    params = [
        resolve_ref(spec, param["$ref"]) if "$ref" in param else param
        for param in params
    ]
    allowed_path = {p["name"] for p in params if p["in"] == "path"}
    allowed_query = {p["name"] for p in params if p["in"] == "query"}
    supplied_path = dict(path_values)
    if len(supplied_path) != len(path_values):
        raise APIError("Duplicate path parameter")
    if set(supplied_path) != allowed_path:
        raise APIError(
            f"Path parameters must be exactly: {', '.join(sorted(allowed_path)) or '(none)'}"
        )
    if any(name not in allowed_query for name, _ in query_values):
        raise APIError("Unknown query parameter; inspect describe for this operation")
    required_query = {
        p["name"] for p in params if p["in"] == "query" and p.get("required")
    }
    if required_query - {name for name, _ in query_values}:
        raise APIError(
            "Missing required query parameters: " + ", ".join(sorted(required_query))
        )
    path = item["path"]
    for name, value in supplied_path.items():
        path = path.replace("{" + name + "}", quote(value, safe=""))
    return path + ("?" + urlencode(query_values) if query_values else "")


def json_value(value):
    if value == "-":
        return json.load(sys.stdin)
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text())
    return json.loads(value)


def request_body(spec, item, args):
    definition = item["operation"].get("requestBody", {})
    if "$ref" in definition:
        definition = resolve_ref(spec, definition["$ref"])
    content = definition.get("content", {})
    supplied = args.json is not None or args.form or args.upload
    if supplied and not content:
        raise APIError("This operation does not declare a request body")
    if not supplied:
        if definition.get("required"):
            raise APIError(
                "A request body is required; inspect describe for its schema"
            )
        return b"", None, None
    if args.json is not None:
        if args.form or args.upload or "application/json" not in content:
            raise APIError(
                "JSON cannot be combined with form/upload, and must be documented for this operation"
            )
        value = json_value(args.json)
        return (
            json.dumps(value, allow_nan=False).encode(),
            "application/json",
            redact(value),
        )
    fields, uploads = pairs(args.form), pairs(args.upload)
    media = (
        "multipart/form-data"
        if "multipart/form-data" in content
        else "application/x-www-form-urlencoded"
    )
    if media not in content or (uploads and media != "multipart/form-data"):
        raise APIError("Form/upload content is not documented for this operation")
    schema = content[media].get("schema", {})
    if "$ref" in schema:
        schema = resolve_ref(spec, schema["$ref"])
    names = {name for name, _ in fields + uploads}
    unknown = names - set(schema.get("properties", {}))
    if unknown and schema.get("additionalProperties") is not True:
        raise APIError("Unknown form fields: " + ", ".join(sorted(unknown)))
    missing = set(schema.get("required", [])) - names
    if missing:
        raise APIError("Missing form fields: " + ", ".join(sorted(missing)))
    if media == "application/x-www-form-urlencoded":
        return urlencode(fields).encode(), media, redact(dict(fields))
    boundary = "bambuddy-" + uuid.uuid4().hex
    parts, preview = [], {"fields": redact(dict(fields)), "uploads": []}
    for name, value in fields:
        if any(char in name for char in '\r\n"'):
            raise APIError("Invalid multipart field name")
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    size = sum(len(part) for part in parts)
    for name, file_path in uploads:
        file = Path(file_path)
        filename = file.name
        if any(char in name + filename for char in '\r\n"\\'):
            raise APIError("Invalid multipart field or file name")
        size += file.stat().st_size
        if size > MAX_BYTES:
            raise APIError("Request exceeds 128 MiB limit")
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
        )
        parts.extend([file.read_bytes(), b"\r\n"])
        preview["uploads"].append(
            {"field": name, "filename": filename, "bytes": file.stat().st_size}
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"{media}; boundary={boundary}", preview


def ingest(raw, destination, source):
    spec = json.loads(raw)
    version = spec["info"]["version"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        raise APIError("Invalid API version for reference directory")
    for ref in set(refs_in(spec)):
        resolve_ref(spec, ref)
    items = operations(spec)
    encoded = (json.dumps(spec, indent=2, ensure_ascii=False) + "\n").encode()
    folder = destination / version
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "openapi.json").write_bytes(encoded)
    counts = Counter(
        tag for item in items for tag in item["operation"].get("tags", ["untagged"])
    )
    manifest = {
        "version": version,
        "openapi": spec["openapi"],
        "schema_file": f"{version}/openapi.json",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "paths": len(spec["paths"]),
        "operations": len(items),
        "unique_operation_ids": len({i["operation"].get("operationId") for i in items}),
        "schemas": len(spec.get("components", {}).get("schemas", {})),
        "tags": dict(sorted(counts.items())),
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    lines = ["method\tpath\toperation_id\ttags\tsummary"]
    for item in sorted(items, key=lambda i: (i["path"], i["method"])):
        op = item["operation"]
        cells = [
            item["method"],
            item["path"],
            op.get("operationId", ""),
            ",".join(op.get("tags", [])),
            op.get("summary", ""),
        ]
        lines.append("\t".join(" ".join(cell.split()) for cell in cells))
    (destination / "operations.tsv").write_text("\n".join(lines) + "\n")
    return manifest


def camera_cache_path(config):
    """Choose a user-specific temporary location scoped to instance and API key."""
    getuid = getattr(os, "getuid", None)
    user = (
        str(getuid())
        if callable(getuid)
        else hashlib.sha256(str(Path.home()).encode()).hexdigest()[:16]
    )
    identity = config["base_url"] + "\0" + config["api_key"]
    name = hashlib.sha256(identity.encode()).hexdigest() + ".json"
    return Path(tempfile.gettempdir()) / f"bambuddy-camera-tokens-{user}" / name


def prepare_camera_cache(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    getuid = getattr(os, "getuid", None)
    if not stat.S_ISDIR(info.st_mode) or (callable(getuid) and info.st_uid != getuid()):
        raise OSError("Camera token cache is not a private user-owned directory")
    if callable(getuid):
        os.chmod(directory, 0o700)


def read_camera_token(path, now):
    try:
        info = path.lstat()
        getuid = getattr(os, "getuid", None)
        if not stat.S_ISREG(info.st_mode) or (
            callable(getuid) and (info.st_uid != getuid() or info.st_mode & 0o077)
        ):
            return None
        data = json.loads(path.read_text())
        token, issued, expires = data["token"], data["issued_at"], data["expires_at"]
        if (
            isinstance(token, str)
            and token
            and isinstance(issued, (int, float))
            and isinstance(expires, (int, float))
            and issued <= now < expires <= issued + CAMERA_TOKEN_CACHE_TTL
        ):
            return token
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def save_camera_token(path, token, issued):
    fd, temporary = tempfile.mkstemp(prefix=".camera-token-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(
                {
                    "token": token,
                    "issued_at": issued,
                    "expires_at": issued + CAMERA_TOKEN_CACHE_TTL,
                },
                stream,
            )
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def invalid_cached_camera_token(response, raw):
    if response["status"] == 401:
        return True
    if response["status"] == 403:
        try:
            data = json.loads(raw)
            detail = data.get("detail", "") if isinstance(data, dict) else ""
            if isinstance(detail, str):
                detail = detail.lower()
                return "token" in detail and any(
                    word in detail for word in ("invalid", "expired", "not valid")
                )
        except (ValueError, UnicodeDecodeError):
            pass
    return False


def camera_snapshot(spec, printer_id, output, base_url, timeout, dry_run, write):
    """Capture a frame with a private, reusable short-lived camera token."""
    if printer_id <= 0:
        raise APIError("Printer ID must be positive")
    token_operation = select_operation(spec, "create_stream_token")
    snapshot_operation = select_operation(spec, "camera_snapshot")
    token_path = request_path(spec, token_operation, [], [])
    printer = [("printer_id", str(printer_id))]
    if dry_run:
        emit(
            {
                "operations": [
                    {"method": token_operation["method"], "path": token_path},
                    {
                        "method": snapshot_operation["method"],
                        "path": request_path(
                            spec, snapshot_operation, printer, [("token", "********")]
                        ),
                    },
                ],
                "requires_write": True,
                "token_policy": "Reuse a valid cached token; create or refresh if needed",
                "saved_to": str(output),
            }
        )
        return 0
    if not write:
        raise APIError(
            "Creating a camera stream token requires --write; use --dry-run to review"
        )
    if output.exists():
        raise APIError("Output file already exists; choose a new path")

    config = connection(base_url)
    cache_path = camera_cache_path(config)
    try:
        prepare_camera_cache(cache_path.parent)
    except OSError:
        cache_path = None
    cached_token = read_camera_token(cache_path, time.time()) if cache_path else None

    def snapshot(token):
        path = request_path(spec, snapshot_operation, printer, [("token", token)])
        return exchange(config, snapshot_operation["method"], path, timeout=timeout)

    if cached_token:
        response, raw = snapshot(cached_token)
        if invalid_cached_camera_token(response, raw):
            cached_token = None
    if not cached_token:
        issued = time.time()
        response, raw = exchange(
            config, token_operation["method"], token_path, timeout=timeout
        )
        if not 200 <= response["status"] < 300:
            emit({"stage": "create_stream_token", "http_status": response["status"]})
            return 1
        try:
            payload = json.loads(raw)
            token = payload.get("token") if isinstance(payload, dict) else None
        except (ValueError, UnicodeDecodeError):
            token = None
        if not isinstance(token, str) or not token:
            raise APIError("Camera token response did not contain a valid token")
        response, raw = snapshot(token)
    if not 200 <= response["status"] < 300:
        emit({"stage": "camera_snapshot", "http_status": response["status"]})
        return 1
    if not raw.startswith(b"\xff\xd8\xff"):
        raise APIError("Camera snapshot response was not a JPEG image")
    cache_status = "reused" if cached_token else "unavailable"
    if not cached_token and cache_path:
        try:
            save_camera_token(cache_path, token, issued)
            cache_status = "stored"
        except OSError:
            pass
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
    emit(
        {
            "http_status": response["status"],
            "saved_to": str(output),
            "bytes": len(raw),
            "camera_token_cache": cache_status,
        }
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", help="Bambuddy instance URL (defaults to BAMBUDDY_URL)"
    )
    parser.add_argument("--timeout", type=float, default=30)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "info", help="Show pinned API version, counts and provenance offline"
    )
    search = sub.add_parser("operations", help="Search the pinned catalogue offline")
    search.add_argument("search", nargs="?", default="")
    search.add_argument("--tag")
    search.add_argument("--limit", type=int, default=40)
    search.add_argument("--offset", type=int, default=0)
    describe = sub.add_parser(
        "describe", help="Show an operation and all referenced schemas"
    )
    describe.add_argument("operation")
    schema = sub.add_parser(
        "schema", help="Show a named component schema and its dependencies"
    )
    schema.add_argument("name")
    refresh = sub.add_parser(
        "refresh", help="Ingest the live OpenAPI document (or a saved copy)"
    )
    refresh.add_argument("--from-file", type=Path)
    call = sub.add_parser("call", help="Call a documented operation over HTTP/HTTPS")
    call.add_argument("operation")
    call.add_argument("--path", action="append", default=[], metavar="NAME=VALUE")
    call.add_argument("--query", action="append", default=[], metavar="NAME=VALUE")
    call.add_argument(
        "--json", metavar="JSON|@FILE|-", help="JSON body, a file, or stdin"
    )
    call.add_argument("--form", action="append", default=[], metavar="NAME=VALUE")
    call.add_argument(
        "--upload", action="append", default=[], metavar="FIELD=LOCAL_FILE"
    )
    call.add_argument(
        "--output", type=Path, help="Save response; JSON remains redacted"
    )
    call.add_argument(
        "--write", action="store_true", help="Execute an already authorized mutation"
    )
    call.add_argument(
        "--dry-run", action="store_true", help="Preview without contacting Bambuddy"
    )
    camera = sub.add_parser(
        "camera-snapshot", help="Capture a JPEG using a cached temporary camera token"
    )
    camera.add_argument("--printer-id", type=int, required=True)
    camera.add_argument("--output", type=Path, required=True)
    camera.add_argument(
        "--write", action="store_true", help="Allow the temporary token to be created"
    )
    camera.add_argument(
        "--dry-run", action="store_true", help="Preview without contacting Bambuddy"
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.timeout > 300:
        raise APIError("Timeout must be greater than zero and at most 300 seconds")
    if args.command == "refresh":
        if args.from_file:
            raw, source = (
                args.from_file.read_bytes(),
                "saved OpenAPI response from Bambuddy",
            )
        else:
            config = connection(args.url)
            response, raw = exchange(
                config, "GET", "/openapi.json", timeout=args.timeout
            )
            if response["status"] != 200:
                raise APIError(f"OpenAPI fetch returned HTTP {response['status']}")
            source = f"{config['base_url'].rstrip('/')}/openapi.json"
        emit(ingest(raw, API_DIR, source))
        return 0
    manifest = json.loads((API_DIR / "manifest.json").read_text())
    spec = json.loads((API_DIR / manifest["schema_file"]).read_text())
    if args.command == "info":
        emit(manifest)
    elif args.command == "operations":
        if args.offset < 0 or args.limit < 1:
            raise APIError("Offset must be nonnegative and limit must be positive")
        items = [
            item
            for item in operations(spec)
            if args.search.lower() in json.dumps(item, ensure_ascii=False).lower()
            and (not args.tag or args.tag in item["operation"].get("tags", []))
        ]
        emit(
            {
                "total": len(items),
                "offset": args.offset,
                "operations": [
                    {
                        "selector": selector(item),
                        "operation_id": item["operation"].get("operationId"),
                        "summary": item["operation"].get("summary"),
                        "tags": item["operation"].get("tags", []),
                    }
                    for item in items[args.offset : args.offset + args.limit]
                ],
            }
        )
    elif args.command == "describe":
        emit(with_schemas(spec, select_operation(spec, args.operation)))
    elif args.command == "schema":
        emit(with_schemas(spec, resolve_ref(spec, "#/components/schemas/" + args.name)))
    elif args.command == "camera-snapshot":
        return camera_snapshot(
            spec,
            args.printer_id,
            args.output,
            args.url,
            args.timeout,
            args.dry_run,
            args.write,
        )
    elif args.command == "call":
        item = select_operation(spec, args.operation)
        path = request_path(spec, item, pairs(args.path), pairs(args.query))
        body, content_type, preview = request_body(spec, item, args)
        needs_write = (
            item["method"] not in ("GET", "HEAD", "OPTIONS")
            or item["path"] in WRITE_GETS
        )
        if args.dry_run:
            safe_path = request_path(
                spec,
                item,
                [
                    (key, "********" if SECRET.search(key) else value)
                    for key, value in pairs(args.path)
                ],
                [
                    (key, "********" if SECRET.search(key) else value)
                    for key, value in pairs(args.query)
                ],
            )
            emit(
                {
                    "method": item["method"],
                    "path": safe_path,
                    "requires_write": needs_write,
                    "content_type": content_type,
                    "body": preview,
                }
            )
            return 0
        if needs_write and not args.write:
            raise APIError(
                "This operation requires --write; use --dry-run to review the request"
            )
        if args.output and args.output.exists():
            raise APIError("Output file already exists; choose a new path")
        response, raw = exchange(
            connection(args.url),
            item["method"],
            path,
            body,
            content_type,
            args.timeout,
        )
        status = response["status"]
        try:
            value = redact(json.loads(raw))
            output = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
            is_json = True
        except (ValueError, UnicodeDecodeError):
            value, output = None, raw
            is_json = False
        if not 200 <= status < 300:
            # Never print arbitrary HTML, binary content or credential-bearing headers.
            emit(
                {
                    "http_status": status,
                    "error": value if is_json else "Non-JSON error response",
                }
            )
            return 1
        if args.output:
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(output)
            emit(
                {
                    "http_status": status,
                    "saved_to": str(args.output),
                    "bytes": len(output),
                }
            )
        elif is_json:
            emit(value)
        elif raw:
            raise APIError("Non-JSON response; use --output FILE to save it")
        else:
            emit({"http_status": status})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (APIError, OSError, ValueError, KeyError) as exc:
        print(f"bambuddy-api: {exc}", file=sys.stderr)
        sys.exit(2)
