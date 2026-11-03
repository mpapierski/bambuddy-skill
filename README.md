# Bambuddy skill for AI agents

A skill for AI agents to inspect and manage a Bambuddy instance through its REST API. It covers printer status, library files, STL/3MF uploads and slicing, and print queues.

## Install in Codex

```sh
git clone https://github.com/mpapierski/bambuddy-skill.git ~/.codex/skills/bambuddy
```

The skill uses two connection inputs: `BAMBUDDY_URL` for the instance URL and `BAMBUDDY_API_KEY` for its API key. Set them in your environment before making API calls. The key is sent as `X-API-Key`. The agent can use `$bambuddy` after the skill is installed.

For direct CLI use, from the skill directory:

```sh
scripts/bambuddy-api operations slice
scripts/bambuddy-api describe slice_library_file
scripts/bambuddy-api call list_printers
```

API discovery works offline using the included Bambuddy 1.2.5.7 OpenAPI snapshot. Live operations use HTTP/HTTPS directly and local uploads are sent as multipart requests. A running instance may expose a different API version; refresh the definitions from the supplied instance when needed.

The included OpenAPI snapshot is generated from [Bambuddy](https://github.com/maziggy/bambuddy), which is licensed under AGPL-3.0. This repository is distributed under the [GNU AGPL-3.0](LICENSE).
