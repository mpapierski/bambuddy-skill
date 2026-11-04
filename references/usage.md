# API client usage

`scripts/bambuddy-api` uses Python's standard library to make direct HTTP/HTTPS
requests from the local machine to Bambuddy.

## Connection and output

Supply exactly two connection values through the environment:

```sh
export BAMBUDDY_URL='https://bambuddy.example.com'
# Set BAMBUDDY_API_KEY securely in the environment, then:
scripts/bambuddy-api call list_printers
```

`BAMBUDDY_URL` is the instance's base URL, optionally including its deployment
path prefix. `--url URL` before the subcommand can override that same input.
`BAMBUDDY_API_KEY` is sent as `X-API-Key`; the client requires it for live calls
and does not print it. No connection file or extra user-supplied credential is
needed. Offline lookup and dry-runs need neither value.

The client connects directly, verifies HTTPS certificates with the system trust
store and refuses redirects. Ordinary calls do not retry. All client code executes locally;
request bodies carry API data and file bytes. A sign-in redirect, 401/403, or HTML
login page is an access failure, not an API result. Report failures against the
supplied URL instead of adding another transport or authentication mechanism.

JSON responses are printed with sensitive field values masked. Booleans such as
`has_cloud_token` are preserved. `--output FILE` saves JSON with the same masking
or saves binary bytes unchanged; it refuses to overwrite an existing file.
Non-JSON response bodies are not printed to the terminal. Model/G-code downloads
may contain embedded identifiers or private project settings; keep those files
out of commits unless requested.

Requests and responses are limited to 128 MiB each. The HTTP socket timeout is
30 seconds. Use a global `--timeout SECONDS` (up to 300) when appropriate.
This client handles finite
responses: use camera snapshots/status rather than a continuous video stream.
Long slicing is asynchronous; poll the job instead of increasing a slice POST
timeout indefinitely.

## Camera snapshots

Discover the printer ID, then capture a current frame:

```sh
scripts/bambuddy-api camera-snapshot --printer-id 1 \
  --output /absolute/path/printer.jpg --dry-run
scripts/bambuddy-api camera-snapshot --printer-id 1 \
  --output /absolute/path/printer.jpg --write
```

The command stores a short-lived camera stream token in the system temporary
directory selected by Python's `tempfile.gettempdir()`, in a user-scoped folder
and scoped to the Bambuddy instance and API key. This is typically `/tmp` on
Linux and a per-user temporary directory on macOS and Windows; the environment
can change the location. On POSIX systems the folder has mode 0700 and token files
have mode 0600. Windows uses the temporary directory's access controls. The token
is stored unencrypted but is never printed or passed as a command argument.

The client reuses a token for at most 59 minutes (the documented lifetime is 60)
and checks it with the snapshot request. If the cached token is rejected as
invalid, it creates a replacement and retries the snapshot once. Other failures
are reported without a retry. `--write` allows the token-creation POST; it does
not start a print. The JPEG is saved with private permissions on POSIX and
existing files are not overwritten. The OS may clear temporary files, but cleanup
timing isn't guaranteed; if the cache disappears, the client creates a new token.
If the cache is unavailable, the snapshot can still succeed and the response reports
`camera_token_cache: unavailable`.

## Offline discovery

```sh
scripts/bambuddy-api operations makerworld
scripts/bambuddy-api operations --tag printers --limit 20 --offset 20
scripts/bambuddy-api describe upload_file
scripts/bambuddy-api describe 'GET /api/v1/printers/{printer_id}/camera/stop'
scripts/bambuddy-api schema PrintQueueItemCreate
```

`operations` searches paths, IDs, tags and descriptions, reports the total match
count, and defaults to 40 results. Advance `--offset` or narrow the search when
there are more results. `describe` shows parameters, media types, responses and
all transitively referenced schemas, including required fields and enums.
Full IDs and short names are accepted when they identify one route. Equivalent
slash aliases use the shorter URL. Where IDs are duplicated across different
methods or routes, specify the exact `'METHOD /path/template'` selector.

## Parameters and bodies

```sh
scripts/bambuddy-api call get_file --path file_id=23
scripts/bambuddy-api call list_queue --query printer_id=1 --query status=pending
scripts/bambuddy-api call slice_library_file --path file_id=23 \
  --json @/absolute/path/slice-request.json --dry-run
scripts/bambuddy-api call slice_library_file --path file_id=23 \
  --json @/absolute/path/slice-request.json --write
```

Repeat `--path NAME=VALUE` and `--query NAME=VALUE` as needed. Path values are
URL-encoded; repeated query keys are retained for array parameters. The client
rejects unknown/missing parameter names. Query values are transmitted as strings;
use the boolean, numeric, array and enum forms documented by the schema.

`--json` accepts literal JSON, `@FILE`, or `-` for stdin. The client checks body
presence and documented media types, not full JSON Schema/business rules. Read
`describe` and handle Bambuddy's validation errors. JSON request files should
contain only the intended changes; keep credentials out of tracked files.

`--dry-run` constructs and previews the request locally. It masks secret fields
and token parameters and never contacts the instance. All methods other than
GET/HEAD/OPTIONS require `--write`, as do the documented GET actions for stopping
a camera stream and creating a settings backup. These guards do not replace
reviewing the operation's actual effects.

## Multipart uploads and downloads

```sh
scripts/bambuddy-api call upload_file \
  --upload file=/absolute/local/path/model.stl \
  --query generate_stl_thumbnails=false --dry-run
scripts/bambuddy-api call upload_file \
  --upload file=/absolute/local/path/model.stl \
  --query generate_stl_thumbnails=false --write
scripts/bambuddy-api call download_file --path file_id=23 \
  --output /absolute/local/path/project.3mf
```

`--upload FIELD=LOCAL_FILE` reads a file on the machine running this command and
sends multipart bytes in the HTTP request to Bambuddy. No server staging path
is needed. `--form NAME=VALUE` adds documented
text fields or sends an `application/x-www-form-urlencoded` request where that
media type is declared. Binary uploads require a multipart API operation.

## Failures and verification

HTTP errors and redirects return nonzero status with the JSON error when one is
available. Arbitrary HTML/error bodies and response headers are not printed.
Transport failures or timeouts also return nonzero; a timed-out write may already
have succeeded. Read back using the returned/discovered resource ID before a
retry. A successful 202 means accepted asynchronous work, not a completed slice
or started print. Schema presence alone is not proof a route works: the legacy
library direct-print operation is deprecated in the pinned definition; use the
queue API and handle the supplied instance's actual response.
