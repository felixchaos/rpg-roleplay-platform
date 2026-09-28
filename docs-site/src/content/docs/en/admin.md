---
title: "Admin Console"
description: "The Admin Console is the operations dashboard for platform administrators, covering user management, usage analytics, security settings, compliance handling, and achievement catalog editing. Only accounts with the admin role can access it; other users are redirected to a permission-denied page."
---

The Admin Console is the operations dashboard for RPG Roleplay platform administrators, covering user management, usage analytics, security settings, compliance handling, and achievement catalog editing. **Only accounts with the admin role can access it.** All other users are redirected to a permission-denied page.

Entry point: the "System Administration" group in the top navigation, or type a sub-feature name directly in the search box to jump to it.

---

## Sub-page Overview

| Sub-page | Anchor | Purpose |
|---|---|---|
| Deployment Config | `#admin-deploy` | Listen address, CORS, upload limits, SMTP, CAPTCHA |
| User Management | `#admin-users` | View user list, disable/restore, force sign-out, role changes |
| Global Usage | `#admin-usage` | Platform-wide token consumption and API cost summary |
| Audit Log | `#admin-audit` | Administrator action history |
| System Health | `#admin-health` | Database, memory, disk, and process status (refreshes every 30 s) |
| System Logs | `#admin-logs` | View and download runtime logs |
| Registration & Invites | `#admin-registration` | Registration mode toggle, invite code generation and management |
| Security Config | `#admin-security` | Rate limits, password policy, session policy, IP blocklist |
| Maintenance Mode | `#admin-maintenance` | Global maintenance banner toggle and service restart |
| DMCA Takedowns | `#admin-dmca-takedowns` | Receive and action DMCA takedown requests |
| DMCA Strikes | `#admin-dmca-strikes` | Strike tracking and account restriction management |
| CSAM Reports | `#admin-csam-reports` | CSAM report queue and disposition |
| AUP Actions | `#admin-aup-actions` | Search users; apply suspend, unsuspend, or permanent termination |
| Feedback Review | `#admin-feedback` | Review queue for user-submitted feedback |
| Achievement Catalog | `#admin-achievements` | Create, edit, and disable achievement definitions |

---

## Common Tasks

### Find and Manage Users

Go to User Management (`#admin-users`). You can search by username and filter by role (admin / regular user) and status (active / disabled). The list shows 20 rows per page, including username, display name, role, status, last login time, 30-day token consumption, and active session count.

Each row supports the following actions (all require a confirmation dialog):

| Action | Description |
|---|---|
| Disable | Prevents login; data is retained and the account can be restored at any time |
| Restore | Re-enables login for a disabled account |
| Force sign-out | Immediately invalidates all sessions for that user |
| Promote to admin | Grants system administration privileges |
| Demote to user | Revokes administration privileges |

Note: you cannot disable or change the role of your own account.

### View Platform-wide Usage

Go to Global Usage (`#admin-usage`) and select a time range in the top-right corner (7 / 14 / 30 / 90 days). The page has four sections:

- **Summary**: total requests, total tokens (input + output), total estimated cost (USD)
- **By user**: per-user token volume, estimated cost, and percentage bar
- **By API**: per-provider token usage and cost
- **Daily trend**: day-by-day token bar chart

### Generate Invite Codes

Go to Registration & Invites (`#admin-registration`) and click "Generate Invite Code". Fill in the quantity (batch generation is supported), validity period (7 / 14 / 30 / 90 / 180 / 365 days), and an optional note, then click "Generate".

The list shows each code's status: available (green), used (grey, with the user who redeemed it), or expired (red). Unused codes can be deleted at any time.

The same page also lets you switch the registration mode:

- **Open**: anyone can sign up.
- **Invite only**: the sign-up form gains an invite code field. A valid invite code, or an email already on the early-access list, is required. A code is used up when the sign-up succeeds; each code works once.
- **Closed**: no new accounts; the "Register" tab on the login page is disabled.

Whether sign-up requires email verification is not set here: server deployments always verify, local / desktop deployments skip it.

### Handle User Feedback

Go to Feedback Review (`#admin-feedback`). The default view shows the "Unreviewed" queue. Click "View" to open the detail panel, which contains:

- Submission time and current review status
- Feedback body text
- Client environment snapshot (URL, app version, viewport, active save/script/turn, recent errors and API failure logs)
- NSFW moderation result (if applicable)
- Conversation excerpt (if applicable)

Available decisions:

| Decision | Description |
|---|---|
| OK | Mark as handled — normal outcome |
| Mark spam | Mark as invalid feedback |
| Terminate account (NSFW) | Requires a reason to be entered below before the button becomes clickable; irreversible — use with caution |

You can also send a reply to the user from within the panel. The reply appears in the user's "My Feedback History" and is independent of the review decision. To retract a sent reply, clear the field and click "Update Reply".

### Manage the Achievement Catalog

Go to Achievement Catalog (`#admin-achievements`) to see all current achievement definitions.

**Create an achievement** — click "New Achievement" and fill in:

- ID (lowercase letters, digits, and underscores; cannot be changed after creation)
- Name and description
- Icon
- Category (Beginnings / Narrative / Exploration / Collection / Perseverance / Hidden)
- Tier (bronze / silver / gold / none)
- Unlock rule: simple mode — pick a metric (e.g. total turns, save count, branch count) + operator (≥ / > / =) + target value; advanced mode — write a composite rule as JSON (`{"all": [...]}` format)
- Hidden achievement: when enabled, unearned users see a redacted placeholder
- Enabled status

**Edit an achievement** — click the inline "Edit" button; changes take effect immediately on save.

**Disable an achievement** — clicking "Disable" removes it from the front end. Users who have already unlocked it are unaffected (achievements are append-only; earned records are never deleted).

### Security Configuration

Go to Security Config (`#admin-security`). There are four sections; click "Save" after making changes:

- **Rate limits**: max requests per IP, max requests per user, time window (minutes). Changes require a service restart to take effect.
- **Password policy**: minimum length, whether digits are required.
- **Session policy**: session timeout in days.
- **IP blocklist**: one IP or CIDR range per line (e.g. `192.168.1.0/24`).

### Enable Maintenance Mode and Restart the Service

Go to Maintenance Mode (`#admin-maintenance`):

- Maintenance toggle: when enabled, all users see the maintenance banner. You can edit the banner text here.
- Service restart: click "Restart Service", confirm in the dialog, and the system sends a graceful reload signal to the backend. Restart completes in approximately 5–15 seconds. It is recommended to enable maintenance mode before restarting.

### Compliance Handling

**DMCA Takedowns** (`#admin-dmca-takedowns`): enter a takedown notice (rights holder name, email, infringing URL, original work description). For each notice you can execute a takedown, reject it, or record a counter-notice (which automatically starts a 10-day timer; the restore action becomes available once the period expires).

**DMCA Strikes** (`#admin-dmca-strikes`): add a strike to a repeat offender's account. Three strikes trigger the account termination flow; the system displays the current risk level.

**CSAM Reports** (`#admin-csam-reports`): log report details and set a disposition (substantiated / escalated / unsubstantiated). For substantiated reports, you must report to NCMEC CyberTipline per the applicable process; the form includes a field for the CyberTip report ID.

**AUP Actions** (`#admin-aup-actions`): search for a username, then choose:
- Suspend (temporary; requires a reason and an optional duration in days)
- Unsuspend
- Permanent termination (irreversible; a reason must be entered before the confirm button becomes clickable)

### View System Logs

Go to System Logs (`#admin-logs`). Select the number of lines to fetch (50 / 100 / 200 / 500) and filter by level (All / ERROR / WARN / INFO). ERROR lines are highlighted red; WARN lines are highlighted orange. Click "Download" to save the current log as a `.log` file.

---

## FAQ

**Q: I'm an admin but visiting `#admin-users` shows a permission-denied error.**

Confirm that your account's role field is actually set to `admin`. Ask another admin to verify your role in the user list, or check the database directly.

**Q: Can a disabled achievement be re-enabled?**

Yes. Disabling marks the achievement as inactive and hides it from the front end. To re-enable it, open the achievement edit dialog, turn on the "Enabled" toggle, and save.

**Q: What categories can I filter in the audit log?**

Filtering by prefix is supported: `user.*` (user-related actions), `config.*` (configuration changes), `maintenance.*` (maintenance mode), `invite.*` (invite code operations), and "All".

**Q: What do I need to do after saving rate limit changes?**

Rate limit configuration requires a backend service restart to take effect. After saving, go to the Maintenance Mode page and restart the service, or restart the process through your operations dashboard.

---

## Related

- [Models & API Settings](/en/settings-models) — User-level model and API key configuration (non-admin)
- [Background Tasks](/en/tasks) — User-facing feedback submission entry point
