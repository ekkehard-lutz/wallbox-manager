# Automatic bundled-card registration

The bundled card uses **one loader: Home Assistant's Lovelace resource collection**.
In normal storage resource mode, setup automatically creates one Resources row of
type `module` at `/wallbox_manager/wallbox-manager-card.js?v=<SHA256 prefix>`.
No manual resource registration or copy to `/config/www` is required. The
integration serves this route without static cache headers. The digest is the
first 12 hexadecimal characters of the bundled file's SHA256 and is recalculated
on setup/reload.

## Lifecycle and migration

Both integration setup and entry setup await registration. The existing manifest
orders setup after configured frontend initialization. A process-wide lock
serializes setup calls, concurrent entries and component-loaded callbacks. The
HTTP route is registered once. If HTTP or Lovelace is not ready, a listener for
HTTP, frontend and Lovelace component-loaded events retries registration. A
headless installation can serve the file without registering a dashboard resource
until Lovelace becomes available.

For a storage resource collection, setup first loads persisted resources through
HA's public collection API. It creates a module resource if none matches the exact
local integration path. Otherwise it reuses the first matching resource ID,
updates a plain/stale URL or incorrect type, and deletes only additional matching
resources. External-host URLs and other paths are preserved. Repeated setup with
an unchanged file makes no resource changes. No `.storage` file is edited directly.

Entry unload retains the shared HTTP route and listener because other entries or
dashboards may use them. Reload reconciles the existing collection and recalculates
the digest; a full restart loads the persisted row and reuses its ID. The old
beta.1 in-memory static-route sentinel is still recognized. There is no extra
frontend module registration or extra-module URL state.

## YAML and non-mutable resource collections

Resource mode, rather than an individual dashboard's mode, determines whether HA
can safely mutate the collection. YAML/non-storage resource collections are left
untouched and a warning explains that automatic registration is unavailable.
For YAML resources, configure the module yourself, for example:

```yaml
lovelace:
  resources:
    - url: /wallbox_manager/wallbox-manager-card.js
      type: module
```

Use a version query suffix when needed to invalidate a cached YAML resource after
an update. The integration does not rewrite YAML or automatically maintain that
suffix. Storage resource mode is recommended for automatic digest management.

## Regression and browser caveats

Commit `4b25359` made the extra frontend module API authoritative and removed
matching Lovelace rows. On the reproduced HA 2026.8 installation the backend and
static route worked, but the card failed; manually restoring a module resource
made it usable. This fix restores the resource collection as the sole loader.
The precise cause of the reported uncaught browser exception is not established
without its stack trace; successful backend setup or an HTTP 200 alone does not
prove JavaScript execution.

After installing this fix, **restart HA** to clear the previous implementation's
in-memory extra-module registration, then fully reload the browser. An integration
reload cannot undefine a custom element already loaded in a browser document.
A page opened before late registration may also need a full reload. Existing
user-managed `/local/...` copies are preserved and should be removed once automatic
loading is verified, to avoid independently loading another card version.

Tests exercise real HA 2026.8 resource collections, including persisted storage,
concurrent/repeated calls, migration, changed content, late initialization and
non-mutable collections. Live browser execution still requires deployment testing.

## Live Home Assistant verification

1. Install the fix, restart HA, and wait for setup. Settings → Dashboards → Resources
   should contain exactly one integration-path row with type JavaScript module
   and the current digest URL.
2. Fully reload the browser and open a card with `type: custom:wallbox-manager-card`.
   Verify rendering, the card picker, and no uncaught card module errors.
   `customElements.get("wallbox-manager-card")` should return a constructor.
3. Repeat integration reload and full HA restart. The resource ID and count should
   remain stable. Verify unrelated resources remain intact.
4. Change/update the bundled JS and reload/restart the integration. Verify the
   resource URL digest changes while its ID stays stable; fully reload the browser.
5. Test a new private browser session and multiple dashboards. For YAML resource
   mode, verify the warning and the manually configured module; YAML stays intact.
