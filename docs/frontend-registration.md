# Automatic bundled-card registration

The bundled card uses **one loader: Home Assistant's extra frontend module API**
(`add_extra_js_url(..., es5=False)`). No manual Lovelace resource or copied JS file
is required in either storage or YAML dashboard mode. It is served at
`/wallbox_manager/wallbox-manager-card.js?v=<SHA256 prefix>` without static cache
headers. The digest is recalculated on setup/reload, so a changed file replaces
the old extra-module URL. Browser reload is required to replace an already defined
custom element after an update.

## Lifecycle and migration

Both integration setup and entry setup await registration. The manifest's existing
`after_dependencies: [frontend]` orders normal startup behind configured frontend
setup. With no frontend manager yet, registration defers to the component-loaded
listener. A single process-wide lock serializes concurrent entries and component
events. The static route is installed once; repeated calls only notify the URL
manager if the current URL is missing. A replacement frontend manager is populated
again. The old beta.1 boolean sentinel means the route already exists, not that
the new module URL has been installed.

Lovelace loading first or later no longer determines card loading. When a storage
resource collection is available, its public collection methods load it and delete
all obsolete local entries for this exact integration-owned route (including old
hashes/duplicates). Their replacement is the current **extra-module** URL, not a
new Resources row. External-host URLs and other paths are untouched. YAML resource
lists are user-owned and remain untouched; they are unnecessary for this card.
Dashboard mode and resource mode are distinct in HA; the collection type controls
migration. No `.storage` file is modified directly.

Entry unload deliberately retains the shared static route, module URL and listener:
other entries/dashboards may still use them, and a loaded browser custom element
cannot be undefined. Reload safely reuses them; a full HA restart rebuilds them.
A headless HA has no module registration until frontend exists. HA safe mode
intentionally omits custom extra modules. YAML mode has no special automatic-load
limitation; manually pinned YAML copies may still load and should not be used to
force an older card version.

## Findings, evidence, and limits

Inspected HA Core **2026.8.0** (the pinned test dependency), specifically frontend
`UrlManager`, `add_extra_js_url`, `remove_extra_js_url`, index rendering and
`frontend/subscribe_extra_js`; Lovelace setup, resource collections and websocket
resource listing. The extra-module set is inserted into a newly rendered index;
subsequent additions notify already subscribed clients. The subscription is for
changes, not a replay of the full set. Therefore a page opened during integration
startup can miss an addition between index rendering and subscription; fully
reload that page after setup completes. No retry can replace a custom element
already defined in that document.

The frontend's [Lovelace panel](https://github.com/home-assistant/frontend/blob/dev/src/panels/lovelace/ha-panel-lovelace.ts)
loads resources once per browser document and starts that loading asynchronously.
Its [resource loader](https://github.com/home-assistant/frontend/blob/dev/src/panels/lovelace/common/load-resources.ts)
uses asynchronous module scripts. A resource listed by the backend, or an HTTP 200,
is not proof that its module executed successfully. The local card defines itself
synchronously without waiting for other HA elements and guards duplicate execution.

Proven issues in the previous implementation: independent extra-module and Lovelace
registration, repeated extra-module add notifications, stale digest on same-process
reload, and retaining multiple same-path resources. **The precise cause of the
reported hardware/browser failure is not proven.** In particular, identical ES
module URLs are normally browser-deduplicated; dual registration alone is not proof
of an execution race. This change removes that ambiguity and makes server-side
registration/migration deterministic. Frontend upstream `dev` inspection explains
the loading design; the deployed frontend version still needs live verification.

Tests use real HA URL managers and resource collections for concurrent/repeated
setup, late components, YAML, old sentinels, duplicate migration, changed content
and replacement managers. Card tests cover duplicate evaluation. Unit tests cannot
prove a browser/proxy/CSP deployment's execution order.

## Live Home Assistant verification

1. Install this branch and restart HA; wait for integration setup to finish, then
   fully reload the browser. Use `type: custom:wallbox-manager-card` in a dashboard.
2. Storage Resources should have **no** entry for the integration-owned route:
   migration removes it automatically. Do not delete/recreate a resource manually.
3. Network inspection should show the current hash URL loading successfully.
   `customElements.get("wallbox-manager-card")` must return a constructor; check
   the console for module/CSP errors, and confirm card rendering and card picker.
4. Repeat after integration reload and full HA restart, with a newly authenticated
   private browser session, two dashboards, and storage/YAML resource modes.
5. On a subsequent JS-content update, restart/reload the integration and fully
   reload the browser. Confirm the hash changes, only one extra-module URL remains,
   and stale storage resource entries do not return.
6. If old `/local/...` copies exist, remove those optional user-created copies once
   automatic loading is verified; this migration never modifies unrelated URLs.

HA documents [extra frontend modules](https://www.home-assistant.io/integrations/frontend/)
and [dashboard resources](https://developers.home-assistant.io/docs/frontend/custom-ui/registering-resources/).
