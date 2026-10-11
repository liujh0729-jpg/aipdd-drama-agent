<!-- lang-switch -->
**English** · [简体中文](../../zh/getting-started/configuring-models.md)

# Configuring Models

DramaClaw CE uses a NewAPI-compatible gateway for text, vision, embedding, image, video, and audio models. Music models are the exception: the **Music** type in settings only registers the model and its site channel, and calls go straight to the vendor's music protocol instead of through the gateway. Model settings are stored in the local CE `settings.db`. Secrets are never returned to the browser; only masked saved-state previews are shown.

After startup, open `http://localhost:8080` and go to **Settings → Models & Channels**. The “Active” badge at the top shows the actual runtime mode, not merely the tab currently being viewed.

## Choose a gateway mode

| Mode | Use case | What you configure |
|---|---|---|
| Official | Use the models provided by the official gateway | DC Key only |
| Custom | Route every model through your NewAPI configuration | NewAPI initialization, providers, feature models, embedding, and media models |
| LocalNewAPI + Official Hybrid | Keep official models while adding Local ComfyUI video models or overriding official models with matching IDs | Official DC Key, LocalNewAPI, ComfyUI URL, and workflows |

Enable the intended mode after configuring it. New jobs read the latest mode, key, and mappings. Jobs already running do not switch gateways midway.

## Official mode

Official mode is the shortest setup path:

1. Open **Official**.
2. Enter your official gateway DC Key.
3. Click **Save & Enable**.

DramaClaw manages the official gateway URL. The official gateway already provides the `DC-*-LLM`, `DC-cognee-embedding`, and official media model mappings, so CE-side upstream mappings are unnecessary.

The official image/video list and its resolutions, aspect ratios, durations, and reference-media capabilities come from the bundled `src/novelvideo/official_media_models.json`.

### Update the official media model catalog

Official and LocalNewAPI + Official Hybrid modes show the current catalog version, model count, and source:

- Click **Check for Updates** to fetch the latest catalog from DramaClaw's official publishing URL and apply it immediately.
- **Automatically update the official model catalog** is off by default. When enabled, the backend checks every five minutes by default and also checks immediately when the corresponding settings panel opens.
- Downloaded catalogs are stored locally at `state/local/official_media_models.json` and remain active after restart.
- DramaClaw rejects a remote catalog older than the active version. After an application upgrade, a newer bundled catalog takes precedence over an older local cache.
- After a successful update, an open XiaHua browser observes the catalog status every minute while automatic updates are enabled and refreshes its image and video model lists when the content SHA256 changes.
- The status API reports the active content SHA256, publishing Git revision, publication time, remote URL, and latest update error so each instance can be audited.

Official catalog updates do not change provider channels, model mappings, or capabilities maintained in Custom mode.

The official catalog defaults to the `dramaclaw-dl` bucket in Chengdu: `https://dramaclaw-dl.oss-cn-chengdu.aliyuncs.com/official-media-catalog/manifest.json`. The manifest points to a SHA256-addressed catalog snapshot that is never overwritten. The backend validates the manifest, catalog version, and content SHA256, and uses ETag revalidation. Override the default with `OFFICIAL_MEDIA_CATALOG_MANIFEST_URL`; `OFFICIAL_MEDIA_CATALOG_URL` remains as a legacy direct-JSON source. Set `OFFICIAL_MEDIA_CATALOG_POLL_SECONDS` to change the polling interval; the minimum is 60 seconds.

The repository's `publish-official-media-catalog` workflow uploads `catalogs/<sha256>.json` with a long-lived immutable cache policy, then publishes `manifest.json` with a 60-second cache policy. Configure the following as repository secrets or in the GitHub `official-media-catalog` environment:

- Variables are optional: the defaults are `oss-cn-chengdu.aliyuncs.com`, `dramaclaw-dl`, and `official-media-catalog`; override them with `OFFICIAL_CATALOG_OSS_ENDPOINT`, `OFFICIAL_CATALOG_OSS_BUCKET`, and `OFFICIAL_CATALOG_OSS_PREFIX`.
- Secrets: `OFFICIAL_CATALOG_OSS_ACCESS_KEY_ID` and `OFFICIAL_CATALOG_OSS_ACCESS_KEY_SECRET` take precedence; otherwise the workflow reuses organization-level `OSS_RELAY_AK` and `OSS_RELAY_SK`.

Keep `dramaclaw-dl` private and grant anonymous `GetObject` only for the `official-media-catalog/*` prefix. The CI identity only needs write access to that prefix. Git pull requests remain the source of truth; enabling OSS versioning is recommended for infrastructure recovery.

To obtain a DC Key, visit <https://susciyuan.com>.

## Custom mode

### 1. Start the local stack

Use the repository compose file (LocalNewAPI is always part of it):

```bash
docker compose up -d
```

It starts the DramaClaw API, web frontend, and LocalNewAPI. DramaClaw normally reaches LocalNewAPI through the container network. The browser-facing host port may differ and does not need to replace the internal URL.

The repository compose file enables the setup and channel-management features required by the settings UI. The LocalNewAPI SQLite database is stored in the Compose `newapi-data` volume. Its path is `/data/one-api.db` inside the LocalNewAPI container, while the DramaClaw API manages it through the shared `/newapi-data/one-api.db` mount. Normally you do not enter a SQLite path or DSN manually.

### 2. Initialize LocalNewAPI

Open **Custom**. When it shows “Initialization required”:

1. Set and confirm a root account password of at least eight characters for a fresh NewAPI instance.
2. Click **Initialize LocalNewAPI**.

Initialization:

- Creates the first NewAPI root account only for a fresh instance.
- Creates or reuses the `dramaclaw-ce-runtime` token.
- Stores the runtime URL and token in CE local settings.
- Verifies the SQLite database and management access.

DramaClaw does not store the root password. Keep it for signing in to NewAPI. Entering a password for an already initialized instance does not reset the existing password.

### 3. Apply the recommended profile

After initialization, start with **Recommended**. One profile configures:

- Provider channels and upstream keys.
- DramaClaw feature-model mappings.
- Cognee embedding model, dimensions, and batch size.
- Image, video, and audio model mappings.

Enter each provider key separately, then click **Save & Apply All**. Keys are stored separately and never written into profile JSON. Leave an already saved key blank; entering a new value replaces it.

The current recommended template uses VolcEngine Ark as its only channel: text, vision, embedding, and Seedance video media models all route through VolcEngine. Speech synthesis (IndexTTS2) routes through the effective NewAPI gateway channel.

The built-in recommended profile is read-only. Switch to **My Config** to edit and persist your own JSON. The main shape is:

```json
{
  "version": 2,
  "name": "My CE profile",
  "channels": [
    {
      "id": "openrouter",
      "provider": "openrouter",
      "baseUrl": "",
      "priority": 0,
      "settings": {}
    }
  ],
  "featureModels": {
    "text": {"channel": "openrouter", "model": "upstream-text-model"},
    "vision": {"channel": "openrouter", "model": "upstream-vision-model"},
    "overrides": {}
  },
  "embedding": {
    "channel": "openrouter",
    "model": "upstream-embedding-model",
    "dimension": 1024,
    "batchSize": 10
  },
  "mediaModels": {
    "my-video-model": {
      "channel": "openrouter",
      "model": "upstream-video-model",
      "mediaType": "video",
      "label": "My Video Model",
      "enabled": true,
      "sortOrder": 100,
      "config": {}
    }
  }
}
```

Each `channel` references a `channels[].id`. A profile currently supports only one entry per `provider`. Switching profiles or viewing JSON only changes the pending template; it does not overwrite the active Advanced Settings. Click **Save & Apply All** to write provider channels, feature models, embedding, and media models to the backend and update the lists below. Saving Advanced Settings synchronizes them into My Config so there are not two competing configurations. After the built-in recommended profile changes, reapply it when Advanced Settings still show an older channel or model. Clearing browser data is not required.

The recommended profile does not create a ComfyUI channel by default. To use local MiniMax H3, open **Manage Channels** in Advanced Settings, add ComfyUI, then load the template from that channel's **ComfyUI Workflows** area. The template adds the workflows and creates a corresponding `MiniMax-H3-local` media-model draft.

### 4. Advanced Settings

Use Advanced Settings to adjust individual results after applying a profile.

#### Provider channels

Click **Manage Channels** to open the provider catalog supported by the current LocalNewAPI instance. Provider types and capabilities are loaded dynamically from `/api/channel/types`, and each provider can be added once. Search by provider name or filter by text, vision, embedding, image, video, audio, and music capabilities. A newly added provider appears first in the configured-channel list. If the catalog fails to load, verify LocalNewAPI is running and click **Reload**.

Provider selectors for feature, embedding, and media models are filtered by their purpose. For example, video models list video-capable providers, while text features list text-capable providers. Existing selections and providers with temporarily unavailable capability metadata remain visible for inspection and correction instead of disappearing after an upgrade.

- **Save Channels** stores CE’s local channel presets.
- **Update NewAPI Channel** immediately replaces the matching NewAPI channel key and Base URL.
- **Base URL Override** is normally empty. Set it only for a custom proxy or when required by the provider.

Before removing a regular provider, the confirmation shows how many feature-model, media-model, and embedding mappings are affected. Removing a saved provider immediately persists the remaining provider list. If that request fails, the local configuration is retained. Removing the final regular provider persists an empty list, so it does not return after refresh. ComfyUI keeps its dedicated cleanup confirmation described below.

After a profile is saved, a channel key should show “Saved” and a masked preview. Password dots without a “Saved” badge indicate an uncommitted browser draft.

#### Feature models

DramaClaw uses stable logical names such as `DC-character-builder-LLM`, `DC-scene-builder-LLM` and `DC-freezone-vision-LLM`. In Custom mode, keep those internal names and map them to real upstream models in NewAPI.

- Text features can use text-only models.
- Vision features send images or video and require a suitable multimodal model.
- Bulk fill changes drafts only; click Save Mapping afterward.
- Hermes may use a separate model. Other `DC-*-LLM` mappings can share one upstream model or be overridden individually.
- After an upgrade that adds a feature row (for example `DC-character-builder-LLM` in v2.0.3), the new row starts empty and is not written to NewAPI until you pick a model and click Save Mapping, or re-apply a quick profile. Until then that feature fails with `No available channel for model DC-...`.

#### Capability slots and one-click binding

`DC-*-LLM` names are the logical models inside a channel. Above them sits a layer of **capability slots**, each one bound to a concrete job (story extraction, script, characters, scenes, storyboard, visual gating, style references, and so on).

- **The source of truth is this project's settings store.** Slot-to-model alias and media mappings live in settings; the NewAPI channel is only a projection. Model names and credentials are always resolved from Settings — `.env` does not decide which model runs (the few direct-egress side paths are the exception; see the exception section in the [environment variable reference](../reference/environment-variables.md)).
- **Pre-run settings → Chain models**: pick a model once and every slot in the chain points at it (story, script, assets, storyboard, style). Only names that actually exist in that provider's catalog get written. Slots that must read images are filled from the same channel with a vision-capable model; if none exists the slot stays empty and the reason is reported. Leaving the picker empty keeps your current bindings.
- **Model plaza** (read-only): lists the models this project can call and which capability slots use each one. A slot with no model bound to it is called out there, along with how many capability slots are still missing an alias mapping.

When a slot cannot be resolved the system never falls back to a preset model name: the task fails and says, in the UI language, which capability is unbound and where to bind it. Retrying without binding will keep failing.

#### Embedding

`DC-cognee-embedding` powers the novel knowledge graph and semantic retrieval. Configure:

- The upstream embedding model.
- Its output dimensions.
- Batch size, defaulting to 10.

The model and dimensions are bound when a project is created. Later changes automatically affect only new projects. Clear and rebuild the graph before changing these values for an existing project.

For embedding HTTP 400/422 errors, verify the model supports the configured dimensions and that batch size does not exceed the upstream `input` limit.

#### Image, video, audio, and music models

Media model configuration controls:

- Whether the model appears in XiaHua.
- Label and ordering.
- The upstream model sent to NewAPI.
- Resolution, aspect-ratio, image-quality, and duration controls.
- Text-to-video, first frame, first/last frame, image reference, all-reference, and video-edit modes.
- Reference image/video/audio limits.
- Human-review control visibility.
- Model-specific request parameters.

Music entries only pick the model and the site channel: the gateway has no music route, so saving one never appends an `audio/speech` endpoint suffix, and the Music Studio tool calls the channel key over the vendor protocol directly. Note this is unrelated to the local ComfyUI `MiniMax-H3-local` template, which is a self-hosted video workflow name. Built-in mainline models provide the default capability baseline and cannot be removed from the configuration. My Config may add image or video models and edit custom-model capabilities. Save the complete configuration and refresh XiaHua to load the latest catalog and controls.

The **Model ID** is DramaClaw’s stable identifier. **Upstream Model** is the actual model used by the NewAPI channel; the two may differ.

### 5. ComfyUI Configuration

In **Custom** mode, add ComfyUI through **Advanced Settings → Provider Channels**. In **LocalNewAPI + Official Hybrid**, add it through the separate **ComfyUI Configuration** section. Both modes use the same LocalNewAPI and SQLite data and the same channel editor.

Click **Manage Channels** and add ComfyUI from the provider catalog to reveal the **ComfyUI Workflows** area beneath that channel. **Load MiniMax H3 Template** appears only there, so users who do not add ComfyUI never see the shortcut. Loading the template adds three starter workflows and fills `http://127.0.0.1:8188` when Base URL is empty. It does not replace a non-empty URL or an existing workflow with the same ID. Save the channel and video-model configuration afterward to persist the result to LocalNewAPI.

Each ComfyUI channel configuration needs:

- One model name used by DramaClaw. It is registered in LocalNewAPI and displayed in XiaHua.
- The ComfyUI service URL; the local default is `http://127.0.0.1:8188`.
- One or more workflows. Each workflow has a unique **Workflow ID** and a ComfyUI **API Format Workflow JSON** export; browser workflow JSON is not accepted.
- Media capabilities for the model, including modes, ratios, resolutions, durations, and reference-media limits.

A normal local ComfyUI instance does not require an API key, so leave it empty. Authentication is relevant only when ComfyUI is placed behind an authenticated proxy.

One model name can bind multiple workflows. DramaClaw saves the model name, Workflow IDs, and Workflow JSON to NewAPI, while the official gateway selects the workflow for each request. XiaHua displays one unified model rather than one model per workflow.

The MiniMax H3 template uses the model name `MiniMax-H3-local` and includes text-to-video, first-frame, and all-reference workflows. Its initial media capabilities enable those three modes, resolutions `480p`, `768p`, and `1080p`, and ratios `21:9`, `16:9`, `4:3`, `1:1`, `3:4`, and `9:16`. These are starter values and remain editable in media model capabilities.

Deleting one workflow removes only that route; it does not automatically delete the unified model. To remove everything, use **Clear ComfyUI configuration**, which appears only after ComfyUI is configured. After confirmation, it removes the ComfyUI channel, workflows, and related media model mappings from local settings and NewAPI. Existing projects and generated media are preserved.

## LocalNewAPI + Official Hybrid mode

Hybrid mode keeps the official gateway while generating selected video models through Local ComfyUI:

1. Save the DC Key under **Official**.
2. Initialize NewAPI once under **Custom**; the same SQLite database is reused.
3. Open **LocalNewAPI + Official Hybrid**.
4. Open the separate **ComfyUI Configuration** section, click **Manage Channels**, and add ComfyUI from the catalog.
5. Load the MiniMax H3 template from that channel's **ComfyUI Workflows** area. Alternatively, enter a local video model name and add one or more Workflow IDs with **API Format Workflow JSON**. The local default URL is `http://127.0.0.1:8188`.
6. Save video configuration and enable Hybrid mode.

The ComfyUI API key is optional. Workflows must use API Format, not the browser workflow format. Custom and Hybrid modes share the ComfyUI channel, workflows, and media capabilities; saving them in either mode updates the same configuration.

While the ComfyUI channel exists, the MiniMax H3 template button remains in that channel's **ComfyUI Workflows** area so missing templates can be restored. Loading it again merges the templates into existing workflows and preserves user-configured workflows with the same Workflow IDs. If the ComfyUI URL is empty, the UI fills `http://127.0.0.1:8188`; it does not replace a non-empty custom URL.

Routing is based on model ID. A Local ComfyUI model may appear in XiaHua as a new model; if it shares an ID with an official video model, the local model overrides that official model. All other models continue through the official gateway. Saving video configuration persists the ComfyUI channel first and then its media models. DramaClaw does not automatically fall back to the official model after a local failure. The user chooses whether to retry or switch models. Hybrid mode manages Local ComfyUI video models only, so it does not require OpenRouter, VolcEngine, or other official upstream provider settings.

## Reference media storage

Image editing, video first/last frames, reference images, and identity images require upstream services to read local files. Go to **Settings → Media Storage** and configure a publicly reachable temporary media relay.

### Aliyun OSS

Provide a Bucket and an AccessKey with read/write permission for that Bucket. A narrowly scoped RAM sub-account is recommended.

| UI field | Environment variable | Example or notes |
|---|---|---|
| Endpoint | `OSS_RELAY_ENDPOINT` | `oss-cn-chengdu.aliyuncs.com`, without `https://` |
| Bucket | `OSS_RELAY_BUCKET` | Temporary-media Bucket |
| AccessKey ID | `OSS_RELAY_AK` | Bucket-scoped AK |
| AccessKey Secret | `OSS_RELAY_SK` | Matching SK |
| TTL | `MEDIA_RELAY_TTL_SECONDS` | Default: 1800 seconds |

The Bucket does not need public-read access. DramaClaw generates temporary signed URLs for upstream access.

### Cloudinary

Enter Cloud name, API Key, API Secret, and an optional folder. Find them under **Product environment settings → API Keys** in Cloudinary. Saved database settings take precedence over environment variables, and full secrets are never returned to the frontend.

## Troubleshooting

| Symptom | What to check |
|---|---|
| No “Saved” badge after saving a key | The value may still be a browser draft. Save/update that channel or reapply the complete profile, and ensure the running image contains the latest code. |
| Adding a media model says its provider key is missing | The provider channel was not persisted to NewAPI. Save/update the channel before saving media models. |
| NewAPI reports `No available channel for model ...` | Check the logical mapping, channel status, upstream model name, and group. |
| Structured steps fail with `Exceeded maximum output retries` behind a Codex-backed relay (Codex2API etc.) | The relay drops `tool_calls` on `/v1/chat/completions`. Enable **ChatCompletions → Responses Compatibility** for that channel in the NewAPI admin. See the [troubleshooting guide](../guides/troubleshooting.md#model--gateway). |
| LocalNewAPI initialization fails | Check the LocalNewAPI service, SQLite mount, directory permissions, and `NEWAPI_PROVISIONER_ENABLED`. |
| A new model is absent from XiaHua | Verify it is enabled, has the correct media type, the complete configuration was saved, and the page was refreshed. |
| Controls do not match model capabilities | Check the media model `config`, especially resolutions, ratios, modes, and reference limits. |
| Knowledge-graph embedding fails | Check the key, upstream model, dimensions, and batch size. HTTP 429 means upstream rate limiting. |
| Reference media cannot be read | Verify media storage and that the upstream service can reach the temporary public URL. |
| Hybrid local video fails without official fallback | Expected: Hybrid mode has no automatic failure fallback. |
| ComfyUI cannot connect to `127.0.0.1:8188` | `127.0.0.1` means the environment running the DramaClaw backend. For containers or remote deployments, use a host or LAN address reachable from that backend. |
| A MiniMax H3 workflow reports missing nodes or models | Install the custom nodes and model files referenced by the recommended workflow, then adjust the workflow for local filenames and versions. |

## Related files

- `src/novelvideo/official_media_models.json`: CE official media models and capabilities.
- `.env.example`: environment variable reference.
- `docker-compose.yml` (source build) / `docker-compose.release.yml` (images): the deployment files (api + LocalNewAPI + web); the gateway mode is chosen in Settings.
- [Self-Hosting Handbook](../guides/self-hosting.md)
- [Environment Variable Reference](../reference/environment-variables.md)
