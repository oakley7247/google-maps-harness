# google-maps-harness

Thirteen read-only Google Maps Platform tools for an agent: where a place is, what is around it, how long it takes to get there, and what the conditions are when it arrives.

Two ways to install it, both from this repo:

| | For | Setup |
| --- | --- | --- |
| **Skill** | claude.ai and Claude Code | Upload a zip. Needs a domain allowlist entry on claude.ai. |
| **(MCP) Model Context Protocol server** | Claude Code, Claude Desktop | A venv and one registration command. |

They expose the same capability with the same controls. The Skill exists because claude.ai cannot launch a local server; the MCP server exists because it is the better fit where a local process is fine.

Nothing here writes, so the risk this manages is not damage — it is cost, context, and trust in what Google returns.

## What an agent gets

| Tool | Answers |
| --- | --- |
| `geocode_address` | Where is this address, landmark, or plus code? |
| `reverse_geocode` | What is at this coordinate? |
| `geocode_place_id` | Where exactly is this place id? |
| `validate_address` | Is this address real and deliverable? |
| `search_places_by_text` | Which places match "ramen near Union Square"? |
| `search_places_nearby` | What is inside this circle, by category? |
| `get_place_details` | Hours, rating, price, phone, website for one place. |
| `autocomplete_places` | What did the user probably mean? |
| `compute_route` | How long from A to B, with traffic? |
| `compute_route_matrix` | Which of these ten is nearest to which of those ten? |
| `get_time_zone` | What time is it there, right now or on a given date? |
| `get_elevation` | How high is this point, or this profile of points? |
| `get_air_quality` | What is the air like there, and who should be careful? |

`compute_route_matrix` is the one built for deciding rather than looking up: one call ranks every candidate against every option, and one bill.

The Skill exposes the same thirteen as subcommands — `geocode`, `search-nearby`, `matrix` — plus a `check` command that diagnoses its own setup.

## Step 1 — Google Cloud (both paths need this)

### Enable the APIs

Enable these at [Maps APIs → API list](https://console.cloud.google.com/google/maps-apis/api-list) on a project with billing on. Maps Platform refuses every request without a billing account attached, even inside the free monthly credit.

| API | Tools it unlocks |
| --- | --- |
| Geocoding API | `geocode_address`, `reverse_geocode`, `geocode_place_id` |
| Places API **(New)** | the four `*_places*` tools |
| Routes API | `compute_route`, `compute_route_matrix` |
| Time Zone API | `get_time_zone` |
| Elevation API | `get_elevation` |
| Address Validation API | `validate_address` |
| Air Quality API | `get_air_quality` |

Watch for **Places API (New)** specifically — the console also lists a legacy "Places API", and enabling only that one produces a 403 on all four place tools.

Enable only what you want reachable. A tool whose API is off refuses with a message naming it, which is a fine way to run a smaller surface deliberately.

### Create and restrict the key

At [Maps Platform credentials](https://console.cloud.google.com/google/maps-apis/credentials), create an API key and set both restrictions before leaving the page:

- **Application restrictions → None.** This key is used by a server-side process, not a browser or a phone. HTTP-referrer and Android/iOS restrictions all fail for it.
- **API restrictions → Restrict key**, ticking exactly the APIs above. An unrestricted key bills for every Google API on the project, so a leaked one is an open account rather than a capped one.

Set a daily quota cap on each API's Quotas page too. Google enforces that at its own edge, which is the only place a spend cap holds no matter what runs on the client.

## Step 2a — Install as a Skill

### Build the package

```bash
python3 skill/build.py                     # dist/google-maps.zip — no credential
python3 skill/build.py --with-key .env     # also dist/google-maps-personal.zip
```

Two builds, because claude.ai has nowhere to keep a key: no environment to set and no persistent home, so an uploaded key file lasts one conversation. The personal build bundles the key inside the Skill, which is uploaded once and stays.

| Build | Contains | Upload to |
| --- | --- | --- |
| `google-maps.zip` | no credential | anyone you share it with |
| `google-maps-personal.zip` | your key | your own account, only |

That convenience is a real exposure — the key then lives in the Skill artifact under Anthropic's standard retention — so the two are kept apart by machinery rather than by care: different filenames, a do-not-share banner in the personal build's own SKILL.md, `*-personal.zip` gitignored wherever it is written, and `tests/test_skill_build.py` asserting no key reaches the shareable build even when one is sitting in the working tree.

### Install on claude.ai

1. **Settings → Capabilities → Skills**, upload the zip.
2. **Settings → Capabilities → Code execution → Domain allowlist**, add these six:

   ```
   geocode.googleapis.com
   places.googleapis.com
   routes.googleapis.com
   addressvalidation.googleapis.com
   airquality.googleapis.com
   maps.googleapis.com
   ```

Step 2 is not optional and is the one that catches people. The sandbox reaches the internet through a managed proxy that allows only listed domains; on the default setting (`Package managers only`) every Maps call fails identically with a 403 on the CONNECT tunnel, no matter how good the key is.

Six named hosts beat `All domains`. That allowlist is the only thing between a sandbox running model-written code and the open internet, and widening it to everything to fix one Skill spends a control you do not get back cheaply. Per-domain lists are an organization-level feature; if your plan only offers all-or-nothing, that is the trade in front of you.

If the sandbox routes through a proxy the script would otherwise ignore, add `--use-proxy` (or set `GOOGLE_MAPS_USE_PROXY=1`). See [Why `--use-proxy` exists](#why---use-proxy-exists).

### Install in Claude Code

```bash
cp -R skill/google-maps ~/.claude/skills/
```

Full network access, nothing to allowlist.

### Verify

Ask Claude to run the Skill's check, or run it yourself:

```bash
python3 skill/google-maps/scripts/maps.py check --all
```

```
KEY       bundled with the skill (39 characters, fingerprint 28988599)
PROXY     none in the environment; requests go direct
NETWORK   reachable

OK  Geocoding            enabled
OK  Places (New)         enabled
...
```

It separates the three failures that look identical from outside — no key, no route, and an API switched off — and prints no part of the key.

## Step 2b — Install as an MCP server

```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.lock
.venv/bin/pip install -e . --no-deps
cp .env.example .env && chmod 600 .env      # then fill in GOOGLE_MAPS_API_KEY
```

```bash
claude mcp add google-maps -- /absolute/path/to/.venv/bin/google-maps-harness --env-file /absolute/path/to/.env
```

The key goes in the file, not on that command line. A value passed with `-e` lands in `~/.claude.json` and in your shell history, and neither is owner-only. The server never reads a `.env` it was not pointed at, so nothing loads by accident.

Restart the client after changing the code — the registered process loads its modules at startup and will otherwise keep running the old ones.

## Configuration

Every setting is optional except the key. The Skill takes the same choices as command-line flags; `python3 scripts/maps.py --help` lists them.

| Variable | Default | What it does |
| --- | --- | --- |
| `GOOGLE_MAPS_API_KEY` | — | Required. The Maps Platform key. |
| `GOOGLE_MAPS_TIMEOUT_SECONDS` | `10` | Connect and read timeout per request. |
| `GOOGLE_MAPS_MAX_REQUESTS_PER_CALL` | `25` | Upstream requests one tool call may make. |
| `GOOGLE_MAPS_MAX_SECONDS_PER_CALL` | `30` | Wall clock one tool call may spend upstream. |
| `GOOGLE_MAPS_REGION_CODE` | unset | Two-letter region that breaks ties on ambiguous names. |
| `GOOGLE_MAPS_LANGUAGE_CODE` | `en` | Language for place names and route instructions. |
| `GOOGLE_MAPS_ALLOW_ATMOSPHERE_FIELDS` | `false` | Lets place lookups request reviews and editorial summaries. |
| `GOOGLE_MAPS_USE_PROXY` | `false` | Skill only. Route through the proxy the environment names. |

## Controlling what this costs

Google bills per request, and Places bills by the most expensive field you ask for. Three controls sit between an agent and your bill.

- **The detail tier picks the price.** Every Places tool takes `detail`: `essentials` (address and coordinates), `pro` (adds names and business status), `enterprise` (adds hours, ratings, phone, website), `atmosphere` (adds reviews). The agent names a tier and never composes a field mask, so it cannot quietly ask for everything.
- **The `atmosphere` tier is off by default.** It is both the most expensive tier and the one that pulls prose strangers wrote into the model's context. Turn it on deliberately or not at all.
- **Every call has a hard ceiling.** Twenty-five upstream requests and thirty seconds. Each result reports `upstream_requests`, so what a call spent is visible in the answer rather than only in the billing console.

`compute_route_matrix` is capped at 100 origin-destination pairs, well under Google's own 625, because Google bills the matrix per pair.

## Security posture

- **The key is held in one place.** Only the transport attaches it. No tool, and no other module, ever handles it. The Skill additionally refuses to take it as a command-line argument, because argv is visible through `ps` and lands in shell history.
- **Nothing sent can be redirected.** Redirects are refused outright and the host allowlist is checked before every socket opens. Three of these APIs carry the key in the query string, so a followed redirect would hand a billable credential to a stranger.
- **Every error is scrubbed.** The key is registered before any client is built, and every exception leaving a tool passes through the scrubber first.
- **Everything Google returns is labelled untrusted.** Place names, editorial summaries, reviews, and route instructions are written by business owners and by the public. Every response carries a warning telling the model to treat that text as data, and every string is stripped of control characters first.
- **Every argument is validated before it becomes a request.** Coordinates must be finite — a JSON parser will hand you `NaN` if you let it. Place ids are matched against a character class and then percent-encoded into the URL path. Free text is length-capped and refuses control characters.
- **Responses are bounded twice**: 4 MiB off the socket, 96 KiB into the model's context.

### Why `--use-proxy` exists

Proxy environment variables are ignored by default. On an ordinary machine that default protects the key: one such variable would route every request through a host somebody else chose.

A sandbox inverts it. Where a managed egress proxy is the only way out, ignoring it does not protect anything — it guarantees no request ever leaves. So the choice is explicit rather than assumed, and it stays off by default because the environment that needs it knows it does.

Two things still hold with it on: the host allowlist runs before any URL is built, so the code cannot be talked into a new destination, and a CONNECT tunnel keeps the proxy out of the request. A proxy that terminates TLS with its own certificate authority would see the key — in a managed sandbox that is the same party already running the code, but worth knowing rather than assuming.

## What this deliberately does not do

- **No writes.** Google Maps Platform has no meaningful write surface here, and this exposes none.
- **No caller-supplied field masks.** A field mask is an HTTP header value; assembled from model output it is a header injection waiting for the first newline, and a way to ask for the most expensive fields on every call.
- **No local spend ledger.** Google's own per-API daily caps enforce a budget at its edge, which holds regardless of what runs on the client. Duplicating that locally would add state, a lock, and a second number to keep true.
- **No map images or static tiles.** They are bytes a model cannot read, and their URLs carry the key.

## Where it runs

| Surface | Skill | MCP server |
| --- | --- | --- |
| Claude Code | yes, full network | yes |
| Claude Desktop | — | yes |
| claude.ai | yes, once the domains are allowlisted | no — cannot launch a local process |
| Claude API container | no — no network access, not configurable | no |

## Development

```bash
.venv/bin/python -m unittest discover -s . -p "test_*.py"
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy --strict --exclude tests .
```

Tests run offline. Nothing in the suite opens a socket or needs a real key — the fake transport records what a request would have been, which is what lets a test assert on the wire rather than on a mock's call count.

The Skill targets Python 3.9 while the server targets 3.11, because a Skill ships into sandboxes this project does not choose. CI runs the Skill on 3.9 for exactly that reason: a linter once rewrote `timezone.utc` to the 3.11-only `datetime.UTC`, and the suite — which runs on 3.11 and 3.14 — could not see it.

## Licence

MIT.
