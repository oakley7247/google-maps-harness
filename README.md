# google-maps-harness

An (MCP) Model Context Protocol server that gives an agent thirteen read-only Google Maps Platform tools: where a place is, what is around it, how long it takes to get there, and what the conditions are when it arrives.

Every tool is read-only. Nothing here changes anything in the world, so the risk this server manages is not damage — it is cost, context, and trust in what Google returns.

## What an agent gets

| Tool | Answers |
| --- | --- |
| `geocode_address` | Where is this address, landmark, or plus code? |
| `reverse_geocode` | What is at this coordinate? |
| `geocode_place_id` | Where exactly is this place id? |
| `validate_address` | Is this address real and deliverable? |
| `search_places_by_text` | Which places match "ramen near Union Square"? |
| `search_places_nearby` | What is inside this circle? |
| `get_place_details` | Hours, rating, price, phone, website for one place. |
| `autocomplete_places` | What did the user probably mean? |
| `compute_route` | How long from A to B, with traffic? |
| `compute_route_matrix` | Which of these ten is nearest to which of those ten? |
| `get_time_zone` | What time is it there, right now or on a given date? |
| `get_elevation` | How high is this point, or this profile of points? |
| `get_air_quality` | What is the air like there, and who should be careful? |

`compute_route_matrix` is the one built for deciding rather than looking up: one call ranks every candidate against every option.

## Setup

### 1. Enable the APIs and make a key

Enable these in the [Google Cloud console](https://console.cloud.google.com/google/maps-apis/api-list). The server calls six APIs; enable only the ones you want the agent to reach, and the tools for the rest will refuse with a message saying so.

| API | Tools that need it |
| --- | --- |
| Geocoding API | `geocode_address`, `reverse_geocode`, `geocode_place_id` |
| Places API (New) | the four `*_places*` tools |
| Routes API | `compute_route`, `compute_route_matrix` |
| Time Zone API | `get_time_zone` |
| Elevation API | `get_elevation` |
| Address Validation API | `validate_address` |
| Air Quality API | `get_air_quality` |

Then create an API key and **restrict it to exactly those APIs**. An unrestricted key is a key that bills for anything.

Set a daily quota cap on each API in the console as well. Google enforces that at its own edge, which is the only place a cap holds no matter what runs locally.

### 2. Install

```bash
python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements.lock && .venv/bin/pip install -e . --no-deps
```

### 3. Put the key in a file only you can read

```bash
cp .env.example .env && chmod 600 .env
```

Fill in `GOOGLE_MAPS_API_KEY`. The server never reads a `.env` it was not pointed at, so nothing loads by accident.

### 4. Register it with your MCP client

```bash
claude mcp add google-maps -- /absolute/path/to/.venv/bin/google-maps-harness --env-file /absolute/path/to/.env
```

The key goes in the file, not on this command line. A value passed with `-e` lands in `~/.claude.json` and in your shell history, and neither is owner-only.

## Configuration

Every setting is optional except the key.

| Variable | Default | What it does |
| --- | --- | --- |
| `GOOGLE_MAPS_API_KEY` | — | Required. The Maps Platform key. |
| `GOOGLE_MAPS_TIMEOUT_SECONDS` | `10` | Connect and read timeout per request. |
| `GOOGLE_MAPS_MAX_REQUESTS_PER_CALL` | `25` | Upstream requests one tool call may make. |
| `GOOGLE_MAPS_MAX_SECONDS_PER_CALL` | `30` | Wall clock one tool call may spend upstream. |
| `GOOGLE_MAPS_REGION_CODE` | unset | Two-letter region that breaks ties on ambiguous names. |
| `GOOGLE_MAPS_LANGUAGE_CODE` | `en` | Language for place names and route instructions. |
| `GOOGLE_MAPS_ALLOW_ATMOSPHERE_FIELDS` | `false` | Lets place lookups request reviews and editorial summaries. |

## Controlling what this costs

Google bills per request, and the Places API bills by the most expensive field you ask for. Three controls sit between an agent and your bill.

- **The detail tier picks the price.** Every Places tool takes `detail`: `essentials` (address and coordinates), `pro` (adds names and business status), `enterprise` (adds hours, ratings, phone, website), `atmosphere` (adds reviews). Each tier costs more than the one below it. The agent names a tier; it never composes a field mask, so it cannot quietly ask for everything.
- **The `atmosphere` tier is off by default.** It is both the most expensive tier and the one that pulls prose strangers wrote into the model's context. Turn it on deliberately or not at all.
- **Every tool call has a hard ceiling.** Twenty-five upstream requests and thirty seconds, reset per call. Each result reports `upstream_requests`, so what a call spent is visible in the answer rather than only in the billing console.

`compute_route_matrix` is capped at 100 origin-destination pairs, well under Google's own 625, because Google bills the matrix per pair.

## Security posture

- **The key is held in one place.** Only the transport attaches it. No tool, and no other module, ever handles it.
- **Nothing this server sends can be redirected.** Redirects are refused outright, proxy environment variables are suppressed, and the host allowlist is checked before every socket opens. Three of these APIs carry the key in the query string, so a followed redirect would hand a billable credential to a stranger.
- **Every error is scrubbed.** The key is registered before any client is built, and every exception leaving a tool passes through the scrubber first.
- **Everything Google returns is labelled untrusted.** Place names, editorial summaries, reviews, and route instructions are written by business owners and by the public. Every response carries a warning telling the model to treat that text as data, and every string is stripped of control characters before it reaches the model's context.
- **Every argument is validated before it becomes a request.** Coordinates must be finite — a JSON parser will hand you `NaN` if you let it. Place ids are matched against a character class and then percent-encoded into the URL path. Free text is length-capped and refuses control characters.
- **Responses are bounded twice**: 4 MiB off the socket, 96 KiB into the model's context.

## What this deliberately does not do

- **No writes.** Google Maps Platform has no meaningful write surface here, and this server exposes none.
- **No caller-supplied field masks.** A field mask is an HTTP header value; assembled from model output it is a header injection waiting for the first newline, and a way to ask for the most expensive fields on every call.
- **No local spend ledger.** Google's own per-API daily caps enforce a budget at its edge, which holds regardless of what runs on this machine. Duplicating that locally would add state, a lock, and a second number to keep true.
- **No map images or static tiles.** They are bytes a model cannot read, and their URLs carry the key.

## The same thing as a shareable Skill

`skill/google-maps/` packages this server's know-how as an Agent Skill, for people who want the capability without running an MCP server. It carries the same validation, the same bounds, the same field-mask cost tiers, and the same untrusted-data discipline, in one dependency-free Python file that runs from bash.

```bash
python3 -m scripts.package_skill skill/google-maps
```

That produces `google-maps.skill` — a zip. Upload it at claude.ai under Settings → Capabilities → Skills, or drop the folder in `~/.claude/skills/` for Claude Code.

Two constraints worth knowing before sharing it:

- **It needs network access**, which varies by surface. Claude Code has it; claude.ai depends on the user's code-execution settings; the API's container has none, so the Skill cannot work there.
- **Each recipient supplies their own key.** Nothing in the bundle holds a credential, and nothing should — a Skill is readable by everyone it reaches.

## Development

```bash
.venv/bin/python -m unittest discover -s . -p "test_*.py"
```

Tests run offline. Nothing in the suite opens a socket or needs a real key — the fake transport records what a request would have been, which is what lets a test assert on the wire rather than on a mock's call count.

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy --strict --exclude tests .
```

## Licence

MIT.
