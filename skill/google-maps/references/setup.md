# Setup

Two things have to be true before any command works: a key that is authorized for the API you're calling, and a network route from the sandbox to Google. They fail differently, so the error tells you which one you're looking at.

## 1. Create the key

Create it at [Maps Platform credentials](https://console.cloud.google.com/google/maps-apis/credentials) on a project with billing enabled. Maps Platform refuses every request without a billing account attached, even inside the free monthly credit.

Then set two restrictions on the key before leaving the page:

- **Application restrictions → None.** This key is used by a server-side script, not a browser or a phone. HTTP-referrer and Android/iOS restrictions all fail for a command-line client.
- **API restrictions → Restrict key**, ticking exactly the APIs below. An unrestricted key bills for every Google API on the project, so a leaked one is an open account rather than a capped one.

## 2. Enable the APIs

Enable these at [Maps APIs → API list](https://console.cloud.google.com/google/maps-apis/api-list). Enable only what you want reachable; a command whose API is off refuses with a message naming it, which is a fine way to run a smaller surface deliberately.

| API | Commands it unlocks |
| --- | --- |
| Geocoding API | `geocode`, `reverse`, `place-id` |
| Places API **(New)** | `search-text`, `search-nearby`, `place-details`, `autocomplete` |
| Routes API | `route`, `matrix` |
| Time Zone API | `timezone` |
| Elevation API | `elevation` |
| Address Validation API | `validate-address` |
| Air Quality API | `air-quality` |

Watch for **Places API (New)** specifically — the console also lists a legacy "Places API", and enabling only that one produces a 403 on all four place commands.

Set a daily quota cap on each API's Quotas page too. Google enforces that at its own edge, which is the only place a spend cap holds no matter what runs in the sandbox. The script's own 25-requests-per-run cap bounds one runaway command, not one runaway week.

## 3. Check before debugging

```bash
python3 scripts/maps.py check --all
```

Reports the key (length and a hash fingerprint, never the value), whether Google is reachable, and which of the seven APIs answer. Everything below is for acting on what it tells you.

## 4. Give the script the key

In order of preference:

1. **Upload a file.** A text file containing just the key, named `google-maps-key.txt`. The script finds it in the working directory and the usual upload locations, or you can point at it with `--key-file`. This keeps the key out of the conversation transcript.
2. **Set `GOOGLE_MAPS_API_KEY`** in the environment, where the environment is yours to set.

**The maintainer's build may already carry the key.** `check` says `bundled with the skill` when it does, in which case there is nothing to upload and nothing below applies. That build is personal to one account and is not the one that gets shared.

**Otherwise, on claude.ai the key does not survive between conversations.** There is no environment to set and no persistent home directory, so an uploaded file lasts as long as that chat. Attaching the key file to a Project is the way to avoid re-uploading — every conversation in that Project starts with the file available. Otherwise plan on uploading it once per conversation.

A `.env` file in the `NAME=value` form works too — the script reads `GOOGLE_MAPS_API_KEY=...` out of it, so a file copied from a server setup needs no editing.

The key is never accepted as a command-line argument. Command lines are visible to every process on the machine through `ps` and get written into shell history; an environment variable or a file is neither.

## 5. Network access

This is the step that blocks people, and it is not the key. A sandbox reaches the internet through a managed proxy that only allows domains on its list, and `googleapis.com` is not on the default one — so every Maps call fails identically with a 403 on the CONNECT tunnel.

claude.ai offers four modes under **Settings → Capabilities → Code execution**:

| Mode | Reaches | Works here |
| --- | --- | --- |
| Network access disabled | nothing | no |
| Package managers only *(the usual default)* | npm, PyPI, GitHub, Ubuntu, crates | **no** |
| Package managers plus specific domains | those, plus a list you add | yes, once you add the six below |
| All domains | everything but Anthropic's blocklist | yes |

Add exactly these, which is the whole surface this Skill talks to:

```
geocode.googleapis.com
places.googleapis.com
routes.googleapis.com
addressvalidation.googleapis.com
airquality.googleapis.com
maps.googleapis.com
```

Six named hosts beat "All domains". The allowlist is the only thing standing between a sandbox running model-written code and the rest of the internet, and widening it to everything to fix one Skill spends a control you cannot get back cheaply.

Per-domain lists are an organization-level feature. If your plan only offers all-or-nothing, that is the trade in front of you.

### Where each surface stands

| Surface | Network | Notes |
| --- | --- | --- |
| Claude Code | Full | Same as any program on the machine. |
| claude.ai | Whatever the setting above says | Default blocks this Skill. |
| The API's code execution container | None, not configurable | This Skill cannot work there. |

### `--use-proxy`

Off by default, and the default is right almost everywhere. On an ordinary machine the proxy environment variables are ignored on purpose: one of them would route every request — API key included — through a host somebody else chose.

A sandbox inverts that. Where the managed proxy is the only way out, ignoring it does not protect the key, it guarantees nothing ever leaves. Turn it on with `--use-proxy`, or by setting `GOOGLE_MAPS_USE_PROXY=1`.

`check` prints which way requests are going, so you never have to guess:

```
PROXY     environment names one (HTTPS_PROXY=http://proxy:8080); requests go DIRECT — proxy ignored
```

That line and `NETWORK unreachable` together mean re-run with `--use-proxy`. `requests go through the proxy` with `NETWORK unreachable` means the domain allowlist, not the routing.

Two things still hold with the proxy on: the host allowlist inside this script is checked before any URL is built, so it cannot be talked into a new destination; and a CONNECT tunnel keeps the proxy from reading the request. A proxy that terminates TLS with its own certificate authority would see the key — in a managed sandbox that is the same party already running this code, but worth knowing rather than assuming.

## Reading the failures

| Message | What it means | What fixes it |
| --- | --- | --- |
| `No Google Maps API key found` | Nothing in the environment or the searched files. | Upload the key file, or pass `--key-file`. |
| `not authorized for this API, or the API is not enabled` | The key works; that API is off. | Enable it in the console. |
| `over its quota` | The daily cap was reached. | Wait, or raise the cap. Do not retry in a loop. |
| `rate-limited` | Too many requests too fast. | Wait. A retry loop is how a key gets suspended. |
| `Could not reach` | No route out. | Read the PROXY line from `check`: either add `--use-proxy`, or add the googleapis.com hosts to the sandbox's domain allowlist. |
| `redirected. Refused` | A host tried to redirect a credential-bearing request. | Nothing to fix; the refusal is the control working. |
