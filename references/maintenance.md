# Definition provenance and maintenance

The bundled definition is a captured `GET /openapi.json` response for Bambuddy
1.2.5.7 using OpenAPI 3.1.0. It provides offline discovery; a supplied instance
may run a different version or configuration.

[The manifest](api/manifest.json) records capture time in UTC, source, original
response SHA-256, formatted snapshot SHA-256, counts, tags and active schema path.
The [full snapshot](api/1.2.5.7/openapi.json) includes every path, operation,
request/response schema, security definition and description. The
[TSV index](api/operations.tsv) retains every method/path binding for `rg` lookup.
It is generated from the snapshot; do not hand-edit it.

There are 789 bindings but 785 distinct operation IDs: the live definition has
duplicate IDs as well as distinct IDs for slash aliases. Do not key a catalogue
only by operation ID or silently drop methods. Counts grouped by tags can exceed
the operation count because one operation can have more than one tag.

After an application update, or when a response no longer matches the reference:

```sh
scripts/bambuddy-api call health_check_health_get
scripts/bambuddy-api refresh
scripts/bambuddy-api info
git diff -- references/api
```

`refresh` reads `/openapi.json` over direct HTTP/HTTPS and updates repository
reference files. It
verifies local schema references, writes a versioned `openapi.json`, updates the
manifest and regenerates the index. For a reviewed saved response, use
`refresh --from-file FILE`. Compare changes before relying on modified write
operations; refresh is not a compatibility test. If the version/counts change,
update the short context in `SKILL.md` and the version link here. Old versioned
snapshots remain available until deliberately retired.

Schema lookup, operation search and dry-runs are offline. Live `call` and
`refresh` commands require only `BAMBUDDY_URL` and `BAMBUDDY_API_KEY`.
If authentication blocks that endpoint, retain the current snapshot and report
the access issue; do not obtain it by running code on the server.
Keep runtime data, printer credentials, downloaded private projects and API keys
out of reference ingestion and tracked configuration. No remote mutation or
physical print is needed to refresh or validate the catalogue.
