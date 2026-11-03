# From a model file to a print

Use `describe`/`schema` for exact fields rather than copying defaults across
printers. The steps below explain the resource relationships; run mutations only
within the user's requested scope. Do not start a print to validate this skill.

## Choose and upload the actual source

An STL contains geometry, not the designer's machine/process/material settings.
A 3MF project may contain those settings, per-object overrides, plate layouts and
multiple filament slots. Prefer the designer's project when preservation matters.

For a model page such as Printables, obtain the selected downloadable STL/3MF
from the provider's actual download flow or API. A model-page URL is not file
bytes; do not invent CDN URLs from an ID or silently choose a different model,
variant, license or print profile. If a provider requires a browser login, use
the existing signed-in browser. Verify the downloaded file's format before
uploading; an HTML sign-in page named `.stl` is not a model.

For MakerWorld, the pinned API provides `resolve_url` and `import_instance`:

```sh
scripts/bambuddy-api describe 'POST /api/v1/makerworld/resolve'
scripts/bambuddy-api describe import_instance
scripts/bambuddy-api schema MakerWorldImportRequest
```

The resolver accepts a `url` and returns model/profile/plate information.
Import takes `model_id` and the chosen **`profile_id`**; the old `instance_id`
field is retained but unused by the installed download flow. Omitting the profile
falls back to the first instance, which may be for a P1S. Imports deduplicate by
canonical source URL and can return a file already present for another profile:
inspect the returned file rather than assuming a fresh selected-profile download.
The `source_type` field refers to registered providers; verify provider support
before trying to route a different site's link through this API.

For a local file:

```sh
scripts/bambuddy-api call upload_file --upload file=/absolute/path/model.stl \
  --query generate_stl_thumbnails=false --write
```

Record the returned library file ID. Use `get_file`, `get_library_file_plates`
and `get_library_file_filament_requirements` as relevant. For a correction,
`delete_file` moves an ordinary managed library file to recoverable trash;
external-file records have different deletion semantics. Preserve the original
designer file when preparing a converted copy.

## Select printer, process, filament and plate

```sh
scripts/bambuddy-api call list_printers
scripts/bambuddy-api call list_printer_models
scripts/bambuddy-api call list_unified_presets --output /tmp/bambuddy-presets.json
scripts/bambuddy-api schema PresetRef
scripts/bambuddy-api schema SliceRequest
```

Use the returned preset names/IDs and compatibility information. Source-aware
references have the shape `{"source":"standard","id":"EXACT PRESET NAME"}`;
sources are `standard`, `local`, `cloud` and `orca_cloud`. Local IDs are strings
inside a `PresetRef`. Prefer these references over deprecated `*_preset_id`
fields. Set exactly one representation for each preset.

STL slicing needs a compatible printer preset (including nozzle diameter),
process preset, and filament preset(s). Bed type and temperatures must match the
intended physical plate/material. `filament_presets` and `filament_colours` use
the model's slot order; these are distinct from the final printer's AMS mapping.
Do not infer the physical spool/plate merely from a standard preset name.

`plate` omitted selects plate 1; `0` means all plates; positive values select
that plate. `export_3mf:true` requests a library output containing embedded G-code.
`auto_orient` and `auto_arrange` default false because they change the layout.
Use them only when appropriate to the chosen model and requested result.

For a designer's 3MF, `use_embedded_settings:true` uses its embedded settings
instead of replacing them with the selected triplet. Preset references are
still required by the request validator. This is intended for a matching printer
profile; it does not itself retarget P1S settings to X1C. `design_overrides`
selects declared designer changes carried onto a replacement process profile;
`process_overrides` applies explicit requested edits afterwards. These paths
have different retention semantics. Do not promise 100% preservation without
comparing source and generated machine/process/filament/object configuration.

Use the supplied instance's configured engine. An engine health response or
successful output file alone does not prove correct printer targeting or
setting retention. Check the generated settings rather than assuming a
particular engine version or automatic cross-printer conversion feature.

## Submit once and verify the result

Write the reviewed `SliceRequest` JSON to a local temporary file, then:

```sh
scripts/bambuddy-api call slice_library_file --path file_id=FILE_ID \
  --json @/absolute/path/slice-request.json --dry-run
scripts/bambuddy-api call slice_library_file --path file_id=FILE_ID \
  --json @/absolute/path/slice-request.json --write
scripts/bambuddy-api call get_slice_job --path job_id=JOB_ID
```

Replace the placeholders with discovered IDs. The slice POST returns 202 and a
job ID. Poll that job at a modest interval, with a bounded wait appropriate to
the model. If still running, retain/report the job ID and continue observing;
do not submit duplicate jobs. On failure, inspect its error and source/preset
compatibility before a new attempt.

On completion, record the output library ID and inspect `get_file`/plates and,
where needed, download the resulting 3MF/G-code. Confirm generated printer model,
nozzle, actual start G-code, plate, material/temperature/slot count and requested
process settings. Check `sliced_for_model` and other returned metadata, then
inspect embedded settings for preservation-sensitive work. Never reuse sliced
P1S G-code by changing only a name or model label.

## Prepare or start a queue item

```sh
scripts/bambuddy-api describe add_to_queue
scripts/bambuddy-api schema PrintQueueItemCreate
scripts/bambuddy-api call 'GET /api/v1/printers/{printer_id}/status' --path printer_id=PRINTER_ID
scripts/bambuddy-api call list_queue --query printer_id=PRINTER_ID
```

The current flow is `add_to_queue` with **the sliced output** as
`library_file_id`, a verified printer target and appropriate AMS/plate options.
The legacy `print_library_file` endpoint is deprecated; use the queue API.
If the user only asked to prepare/queue a job for review, set
**`manual_start:true`**: the default is false and an eligible item can dispatch
automatically. Do not set `skip_filament_check` simply to force a mismatch through.

When printing is authorized, verify the intended printer is available, the
selected sliced file is compatible, and the known material/plate/bed state meets
the job's requirements. If a physical condition cannot be established and is
essential, ask for that missing fact; API readiness is not a view of the bed.
Keep the queue item ID returned by creation. For a manually staged item, call
`start_queue_item` with that `item_id` once ready. Poll `get_queue_item` and
`get_printer_status` to distinguish queued, dispatching, running and failed
states. A successful insertion/start response does not prove a physical print
began. After a timeout, inspect those resources before repeating the mutation.
