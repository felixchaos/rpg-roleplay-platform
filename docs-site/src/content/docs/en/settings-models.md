---
title: "Model Configuration"
description: "Add your own API keys for each AI provider so RPG Roleplay can use your account's models for gameplay, extraction, image generation, and more. Only providers with a configured key appear in the list. Keys are encrypted at rest and never shown in plaintext."
---

Add your own API keys for each AI provider so RPG Roleplay can use your account's models for gameplay, extraction, image generation, and more. Only providers with a configured key appear in the list. Keys are encrypted at rest and never shown in plaintext.

Navigation: top bar **Settings** → **Model Configuration**.

---

## Key Concepts

- **BYOK (Bring Your Own Key)**: RPG Roleplay does not bundle shared API keys. You obtain keys directly from each provider and enter them here. Once saved, a key is encrypted and only its last few characters are displayed — it is never echoed back.
- **Provider**: A vendor that supplies AI models, such as OpenAI, Anthropic, or Google AI Studio.
- **Provider toggle**: The switch on the far right of each row controls whether that provider's models appear in the global model selector. When disabled, all of that provider's models are hidden from in-game and in-chat selectors.
- **Per-model visibility**: The **Manage Visible Models** dialog lets you show or hide individual models within a provider. Hidden models are excluded from dropdowns but are not deleted and can be re-enabled at any time.
- **Base URL**: The endpoint for the provider's API. Built-in providers have sensible defaults. If you use a relay or self-hosted proxy, you can override it here. Base URL is a per-user credential field — changes affect only your account.
- **Connection method**: Configured per credential. Options are **Direct**, **HTTP Proxy**, and **LAN**. Selecting **HTTP Proxy** reveals an additional field for the proxy address (e.g. `http://127.0.0.1:7890`). Also per-user.
- **Model sync (sniffing)**: After adding or editing a key, the platform automatically queries the provider for its available model list and syncs locally. You can also trigger this manually.
- **Health status**: The indicator next to each model — green (reachable), yellow (degraded), red (unreachable), grey (not yet tested). Click the status text in the list to trigger a manual sync.
- **Active model source**: The model used in a game or chat session is resolved in priority order: module-level override (highest) → user's default GM model → platform catalog fallback (lowest).

---

## Supported Providers

| Provider | Notes |
|---|---|
| OpenAI | GPT-4o, o-series, etc. — OpenAI-compatible protocol |
| Anthropic | Claude Opus / Sonnet / Haiku — native protocol |
| DeepSeek | DeepSeek R1 / Chat — OpenAI-compatible |
| DashScope (Qwen) | Tongyi Qianwen series — OpenAI-compatible mode (`/compatible-mode/v1`) |
| Hunyuan (Tencent) | Tencent Hunyuan series — OpenAI-compatible |
| xAI (Grok) | Grok series — OpenAI-compatible |
| MiMo (Xiaomi) | Xiaomi MiMo series — OpenAI-compatible |
| Google AI Studio | Gemini series — Base URL defaults to `/v1beta/openai`; a shorter path returns 404 |
| OpenRouter | Aggregation proxy — one key accesses dozens of providers |
| Agent Platform | Authenticates via a Google Cloud Service Account JSON (Vertex AI) |
| Custom | Any OpenAI-compatible endpoint — supply the model ID and Base URL manually |

> **About "OpenAI-compatible"**: any service that implements the OpenAI chat completions format (`/v1/chat/completions`) can be added via the **Custom** provider option, including domestic relay services and local vLLM deployments.

---

## Common Tasks

### Add an API Key for a Provider

1. Click **Add API Key** in the top-right corner of the page.
2. Select your provider from the **Provider** dropdown. The Base URL field auto-fills with the provider's default.
3. Paste your key into the **API Key** field (keys typically start with `sk-`). The key is not echoed back after saving — only the last few characters are displayed.
4. If you use a relay service, update **Base URL** to your relay address. If you need a proxy, set **Connection method** to **HTTP Proxy** and enter the proxy URL (e.g. `http://127.0.0.1:7890`).
5. Click **Add**. The platform syncs the provider's model list automatically, and the provider appears in the configuration list.

---

### Add Google AI Studio (Gemini)

1. Obtain an API key from [aistudio.google.com](https://aistudio.google.com).
2. Select **Google AI Studio** from the provider dropdown. The Base URL auto-fills to `https://generativelanguage.googleapis.com/v1beta/openai`.
3. Enter your API key and click **Add**.

> The Base URL must end with `/v1beta/openai`. Using only `/v1beta` returns a 404. This is the fixed path for Gemini's OpenAI-compatible endpoint.

---

### Add Agent Platform (Vertex AI / Service Account)

1. Select **Agent Platform (Service Account)** from the provider dropdown.
2. Paste the full contents of your Google Cloud Service Account JSON into the text area. A valid SA JSON must include `project_id`, `client_email`, and `private_key`. The dialog validates the JSON in real time.
3. Click **Add**.

---

### Control Which Models Appear in Selectors

**Hide or show individual models:**

1. Click a provider in the configuration list to expand its detail panel at the bottom.
2. Click **Manage Visible Models** in the detail panel.
3. Check or uncheck models in the dialog. A search filter and **Select All** / **Deselect All** buttons are available.
4. Click **Save**. Synced overlay models use per-user visibility — re-syncing does not reset your selections.

**Hide an entire provider:**

Toggle the switch on the far right of the provider row. When disabled, all of that provider's models are removed from every in-game and in-chat model selector.

---

### Update a Key or Change the Base URL / Connection Method

1. Click the provider in the list to open the detail panel.
2. Click **Edit**.
3. Update the Base URL, connection method, or API key (leave the key field blank to keep the existing key).
4. Click **Save**.

---

### Delete a Key

In the detail panel, click **Delete Key** and confirm. The provider is removed from the list and its models become unavailable.

---

### Manually Sync or Validate the Remote Model List

1. Click **Validate Connection** in the detail panel, or click the connectivity status text in the list row.
2. The dialog probes the provider and shows a diff of local vs. remote models:
   - **New on remote**: models available remotely but not locally
   - **Local only**: models that exist locally but are no longer available remotely
3. Regular users: the diff is against your own model list — click **Re-sync into my list** to bring in new remote models; select obsolete entries and click **Delete N** to remove models you added yourself (built-in catalog models can only be removed by admins). Admins: the diff is against the built-in platform catalog, and **Add All** writes new models into the catalog (visible to everyone). A self-hosted relay is not in the built-in catalog, so when an admin validates one the diff is against their own list and the buttons work as they do for regular users. The footer of the dialog states what the diff is against.

---

### Manually Add a Model (Not in the Synced List)

1. In the detail panel, switch to the **Models** tab and click **Add Model**.
2. Enter the provider's actual model identifier (`real_name`, e.g. `gpt-4o-mini-2024-07-18`), a display name (e.g. `GPT-4o Mini`), select capability tags, and click **Add**.

---

### View API Usage for a Provider

Switch to the **Usage** tab in the detail panel to see request counts, input/output token counts, and estimated costs for the past 30 days. Full records are available on the **Usage** page.

---

## FAQ

**Q: I added a key but the provider shows "unreachable." What should I do?**
A: Click **Validate Connection** for detailed error output. Common causes: incorrect key, wrong Base URL (Google AI Studio requires `/v1beta/openai` at the end), or a missing HTTP proxy for connections to overseas services.

**Q: A provider I disabled still shows models in the selector.**
A: Confirm the switch at the right end of that provider's row in the list is off (grey). The switch only disables your own key for that provider (it stops being used; turn it back on to resume) and does not affect other users. Editing the provider on this page (proxy, address, or a new key) leaves the switch as it is. While it is off, its models are not synced (your renamed and hidden models are kept), and turning it back on syncs once automatically; saving a new key from the mobile web app, the iOS app, or the first-run setup dialog turns it back on. To hide only specific models while keeping the provider active, use **Manage Visible Models**.

**Q: Can I configure multiple providers at the same time?**
A: Yes. Different providers' models can be assigned to different game modules — see [Module Model Assignment](/en/settings-modules).

**Q: How do I use a relay service?**
A: Select the matching provider (or **Custom**), change the Base URL to the relay's OpenAI-compatible endpoint (e.g. `https://your-relay.example.com/v1`), enter the key issued by the relay, and click **Add**.

**Q: How do I use OpenRouter?**
A: Register at openrouter.ai, obtain a key, select **OpenRouter** in the provider list, and enter the key. One key grants access to models from dozens of providers.

**Q: The health status has been "not yet tested" for a while.**
A: Click **Validate Connection** in the detail panel, or click the connectivity status column in the list.

**Q: I hid a model, then re-synced and it came back.**
A: Synced overlay models use per-user visibility — re-syncing does not reset your choices. If a model was added by re-syncing from **Validate Connection**, it defaults to visible; go to **Manage Visible Models** to hide it.

**Q: Does changing Base URL affect other users?**
A: No, and this applies to admins too. Base URL and connection method (proxy) are stored in your personal credentials (per-user) and only affect API calls made under your account. The global catalog is not modified, and a relay you add appears only in your own list.

---

## Security Notes

- Keys are encrypted at rest on the server and never appear in plaintext in the database, logs, or the UI.
- Request keys with minimal permissions — Chat Completions only. Avoid granting Fine-tune, Admin, or Billing access to reduce exposure if a key is compromised.
- If you suspect a key has been leaked, revoke it in the provider's console immediately, then enter a new key here.
- When using **HTTP Proxy**, the proxy URL must be a valid HTTP address (e.g. `http://127.0.0.1:7890`). The backend validates the address before saving.

---

## Related

- [Model Parameters](/en/settings-modelparams)
- [Module Model Assignment](/en/settings-modules)
