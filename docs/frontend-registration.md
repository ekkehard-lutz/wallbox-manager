# Automatic bundled-card registration (beta.2)

The integration serves `/wallbox_manager/wallbox-manager-card.js` with a content
hash in the registered URL. HTTP 200 confirms serving only; it does not establish
that the browser imported the module or defined the custom element.

Registration now uses both Home Assistant's public `add_extra_js_url` module API
and, in storage resource mode, the Lovelace resource collection's
`async_get_info`, `async_create_item` and `async_update_item` APIs. The latter makes
the bundled card visible to the dashboard resource loader, which also worked in
the reported manual-resource workaround. No `.storage` files or dashboards are
edited directly. A same-path resource is reused and updated to the current hash
and module type. User-owned YAML resource lists are not edited; the extra frontend
module supplies the card in that mode.

A component-loaded listener handles late frontend/Lovelace initialization.
Concurrent setup is serialized, the static path is installed once and repeated
setup is idempotent. JavaScript is enclosed in a private scope with an early
custom-element guard, so a temporary manual resource or another URL/cache hash
cannot redeclare globals, redefine the element or duplicate the card registry.
The card defines itself synchronously without awaiting other custom elements.

The previous implementation already requested an ES module, not an ES5 classic
script. It lacked the dashboard resource registration and skipped registration if
the frontend had not yet loaded. The reported browser session is not accessible
here, so the precise original browser-side failure cannot be established from
HTTP status alone. Tests cover real HA URL managers and resource collections,
late initialization, concurrent calls and duplicate script evaluation.

Home Assistant documents [custom resource registration](https://developers.home-assistant.io/docs/frontend/custom-ui/registering-resources/)
and [extra frontend modules](https://www.home-assistant.io/integrations/frontend/).

## Home Assistant verification

1. Install the integration update and restart HA. Reload the browser fully.
2. In dashboard resources (storage mode), confirm one current bundled module URL.
   In the browser network panel confirm its successful load, and check
   `customElements.get("wallbox-manager-card")` returns a constructor.
3. Confirm the card renders and appears in the card picker. Repeat with a fresh
   browser session and after an integration reload.
4. Remove temporary manual entries such as `/local/wallbox-manager-card.js` once
   automatic loading is verified. Keep the current hash-based bundled module.
   If the temporary entry used the exact integration URL, it has been reused;
   removing it requires reloading the integration/restarting HA so the
   automatic resource can be recreated. There is no need for a copied JS file.
5. Refresh again after removing temporary resources. Test two dashboards and a
   browser that previously cached beta.1; check for duplicate-definition errors.

Live browser/HA verification remains necessary: a server-side registration test
cannot prove resource delivery through a particular proxy, browser cache or
Content Security Policy.
