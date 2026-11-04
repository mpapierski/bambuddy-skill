# Bambuddy skill for AI agents

A skill for AI agents to inspect and manage a Bambuddy instance through its REST API. It covers printer status, library files, STL/3MF uploads and slicing, and print queues.

## Install in Codex

```sh
git clone https://github.com/mpapierski/bambuddy-skill.git ~/.codex/skills/bambuddy
```

The skill uses two connection inputs: `BAMBUDDY_URL` for the instance URL and `BAMBUDDY_API_KEY` for its API key. Set them in your environment before making API calls. The key is sent as `X-API-Key`. The agent can use `$bambuddy` after the skill is installed.

## Example prompts

Replace the details in brackets with your model, printer, and material:

- **Check your printers:** “Use `$bambuddy` to list my printers and tell me whether my X1 Carbon is ready to print.”
- **Prepare a model:** “Use `$bambuddy` to download [Printables URL], upload [STL filename], and slice it for my X1 Carbon with [material]. Tell me the estimated print time and filament use. Don't start printing yet.”
- **Start a print:** “Use `$bambuddy` to print [STL filename] from [Printables URL] on my X1 Carbon. Use [material] from AMS slot [number] and [layer height] layers. The [plate type] build plate is clear. Slice the model, start the print, and check that it actually starts.”
- **Check progress:** “Use `$bambuddy` to check my X1 Carbon's current print. What's the progress, current layer, remaining time, and are there any errors?”
- **Inspect filament:** “Use `$bambuddy` to show what filament is loaded in each AMS slot.”
- **Check 3MF settings:** “Use `$bambuddy` to slice [3MF file] for my X1 Carbon. Preserve the designer's settings where possible, compare the source and output settings, and tell me what changed. Don't start printing yet.”

A Printables model page can have multiple files. Specify the file you want; the agent must be able to download it before uploading it to Bambuddy. An STL contains geometry rather than slicer settings, so the agent needs suitable printer, process, and material presets.

For direct CLI use, from the skill directory:

```sh
scripts/bambuddy-api operations slice
scripts/bambuddy-api describe slice_library_file
scripts/bambuddy-api call list_printers
```

API discovery works offline using the included Bambuddy 1.2.5.7 OpenAPI snapshot. Live operations use HTTP/HTTPS directly and local uploads are sent as multipart requests. A running instance may expose a different API version; refresh the definitions from the supplied instance when needed.

The included OpenAPI snapshot is generated from [Bambuddy](https://github.com/maziggy/bambuddy), which is licensed under AGPL-3.0. This repository is distributed under the [GNU AGPL-3.0](LICENSE).
