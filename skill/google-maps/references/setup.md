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

## 3. Give the script the key

In order of preference:

1. **Upload a file.** A text file containing just the key, named `google-maps-key.txt`. The script finds it in the working directory and the usual upload locations, or you can point at it with `--key-file`. This keeps the key out of the conversation transcript.
2. **Set `GOOGLE_MAPS_API_KEY`** in the environment, where the environment is yours to set.

A `.env` file in the `NAME=value` form works too — the script reads `GOOGLE_MAPS_API_KEY=...` out of it, so a file copied from a server setup needs no editing.

The key is never accepted as a command-line argument. Command lines are visible to every process on the machine through `ps` and get written into shell history; an environment variable or a file is neither.

## 4. Network access

Skills run in a sandbox whose network access varies by surface:

| Surface | Network |
| --- | --- |
| Claude Code | Full — same as any program on the machine. |
| claude.ai | Varies with user and admin settings for code execution. |
| The API's code execution container | None. This script cannot work there. |

If every command fails with "Could not reach", the key is not the problem and neither is the project. The sandbox has no route to `googleapis.com`, and the fix is a settings change rather than a retry. On claude.ai the setting lives with the file-creation and code-execution feature; Anthropic's [Create and edit files](https://support.claude.com/en/articles/12111783-create-and-edit-files-with-claude) support article documents the current control.

## Reading the failures

| Message | What it means | What fixes it |
| --- | --- | --- |
| `No Google Maps API key found` | Nothing in the environment or the searched files. | Upload the key file, or pass `--key-file`. |
| `not authorized for this API, or the API is not enabled` | The key works; that API is off. | Enable it in the console. |
| `over its quota` | The daily cap was reached. | Wait, or raise the cap. Do not retry in a loop. |
| `rate-limited` | Too many requests too fast. | Wait. A retry loop is how a key gets suspended. |
| `Could not reach` | No network route. | Sandbox settings, not the key. |
| `redirected. Refused` | A host tried to redirect a credential-bearing request. | Nothing to fix; the refusal is the control working. |
