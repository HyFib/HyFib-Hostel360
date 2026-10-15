# Hostel360 student and parent portal

The portal students and parents use on their phones: fees and online payment, mess menu and
bills, mess reduction, leave with parent approval, grievances, feedback, bonafide certificates
and notifications, in English and Tamil. It is a Vue 3 single-page app served by Frappe at
`/hostel` (design §6 and §7) and installable as a PWA.

| | |
|---|---|
| Stack | Vue 3 (`<script setup>`, TypeScript), vue-router, Tailwind CSS 4, Vite 8, vite-plugin-pwa |
| Tests | Vitest + @vue/test-utils (jsdom) |
| Package manager | yarn 1 with a committed `yarn.lock` (bench uses yarn) |
| Node | 22.12 or later (the dev container and production use Node 24) |

## Everyday commands

```bash
cd frontend
yarn install --frozen-lockfile
yarn dev          # http://localhost:8080/hostel/ with API calls proxied to Frappe
yarn build        # writes ../hostel360/public/frontend and ../hostel360/www/hostel.html
yarn test --run   # unit and component tests
yarn typecheck    # vue-tsc over the app, the tests and the build config
yarn deps:age     # fails if yarn.lock holds a release younger than 14 days
```

In the dev container: `docker exec h360-dev-bench-1 bash -c 'cd /workspace/hostel360/frontend && yarn install && yarn build && yarn test --run'`.

`yarn dev` proxies `/api`, `/assets/frappe`, `/files` and `/private` to
`http://localhost:8000`; set `H360_BACKEND=http://site.localhost:8000` to target another
bench site. Sign in through the portal's own OTP screen; the dev server has no CSRF meta tag,
so the client fetches a token after sign-in (and on the first rejected write).

`bench build` runs the app root's `yarn build`, which builds this folder, and `bench setup
requirements --node` runs the root `postinstall`, which installs it with the frozen lockfile.

## How the page is served

The build writes hashed assets to `hostel360/public/frontend/` (served as
`/assets/hostel360/frontend/`) and turns `index.html` into the Frappe web page
`hostel360/www/hostel.html`. Everything in that page is wrapped in Jinja `raw` blocks except two
values the server fills in; neither is executable script, so a strict CSP holds:

```html
<meta name="csrf-token" content="{{ csrf_token }}">
<script type="application/json" id="h360-boot">{{ boot_json }}</script>
```

`hostel360/www/hostel.py` must provide them and must disable page caching (the CSRF token is per
session):

- `no_cache = 1` (module attribute and `context.no_cache = 1`)
- `csrf_token`: `frappe.sessions.get_csrf_token()` for signed-in users, empty for guests
- `boot_json`: JSON of `{site_name, institution_name, institution_short_name, logo, support_phone,
  support_email, is_logged_in, kind, lang}` with `<`, `>` and `&` escaped as `<`, `>`,
  `&` (Frappe's Jinja does not autoescape)

`hostel360/public/frontend/index.html` (same markup, empty values) is the offline shell the
service worker falls back to; it never contains personal data.

## Web server requirements (production nginx)

| Location | Headers |
|---|---|
| `/assets/hostel360/frontend/sw.js` | `Service-Worker-Allowed: /hostel`, `Cache-Control: no-cache` |
| `/assets/hostel360/frontend/manifest.webmanifest`, `index.html`, `workbox-*.js` | `Cache-Control: no-cache` |
| `/assets/hostel360/frontend/assets/*` (hashed) | `Cache-Control: public, max-age=31536000, immutable` |
| `/hostel`, `/hostel/*` | `Cache-Control: no-store` (rendered per session) |
| `/assets/hostel360/embed/*` | `Cross-Origin-Resource-Policy: cross-origin` (website buttons, see `deploy/integration/website/`) |

Without `Service-Worker-Allowed` the worker cannot control `/hostel`; the portal still works,
just without the offline shell.

Content-Security-Policy for `/hostel` (Razorpay Checkout is loaded only when a payment starts;
with PayU the browser posts a server-signed form to PayU's hosted page, so `form-action` must
list it):

```
default-src 'self';
script-src 'self' https://checkout.razorpay.com https://cdn.razorpay.com;
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob: https://cdn.razorpay.com;
connect-src 'self' https://api.razorpay.com https://lumberjack.razorpay.com;
frame-src https://api.razorpay.com https://checkout.razorpay.com;
worker-src 'self'; manifest-src 'self'; font-src 'self' data:;
base-uri 'self'; form-action 'self' https://secure.payu.in https://test.payu.in;
frame-ancestors 'none'; object-src 'none'
```

`style-src 'unsafe-inline'` is for Razorpay's injected checkout styles; the portal's own styles
are a single stylesheet. `img-src blob:` is for photo previews before upload.

## Payments

`create_payment` answers with `gateway: "razorpay"` (Checkout opens in the page and its signed
response goes to `verify_payment`) or `gateway: "payu"` (`checkout: {action_url, method, fields}`
is posted to PayU as a top-level form, see `utils/redirectCheckout.ts`). PayU sends the payer back
through `hostel360.api.payments.payu_return`, which redirects to `/hostel/fees?payment=<transaction>&status=…`;
the fees page treats the status as a hint and shows what `get_payment_status` confirms.
An invoice with a checkout still open stays payable: paying again resumes the same order.

## Languages

The language switch changes the app's own text (`i18n/`) and the server's: every request
carries `_lang`, and `set_language` saves the choice as the user's language so notifications
follow it too. Server messages are translated in `hostel360/locale/ta.po`.

## Code map

```
src/
  api/          client.ts (fetch wrapper, CSRF, error mapping), auth.ts, portal.ts (one
                function per endpoint in design §6), files.ts (PDF save, photo shrinking), types.ts
  stores/       session (who is signed in, chosen ward), network (offline banner), toast, inbox
  router/       routes.ts, guards.ts (sign-in → consent → confirm details → role → verification)
  i18n/         index.ts, en.json, ta.json (identical keys; a test enforces it)
  components/   shell, navigation, form controls (FormField, OtpInput, StarRating, ToggleSwitch),
                dialogs, lists (InvoiceList, StepTimeline) and states (EmptyState, ErrorState, skeletons)
  views/        one file per route
  utils/        boot payload, money/date and hostel-clock helpers, sanitising admin HTML,
                Razorpay loader, redirect checkout form (PayU)
plugins/frappe-page.ts   builds hostel360/www/hostel.html from the Vite output
scripts/                 app icon drawing, dependency age check
tests/                   Vitest suites
```

## Conventions

- **Text**: every visible string goes through `t()` with a key in both `en.json` and `ta.json`.
  Write natural Tamil, not word-for-word. Server values (statuses, categories) are shown with
  `tv(group, value)`, which falls back to the raw value for anything new.
- **Money and dates**: `formatINR` (₹12,34,567.50) and `formatDate` ("15 Oct 2026") from
  `utils/format.ts`; Frappe datetimes are site time and are never shifted.
- **Errors**: API failures arrive as `ApiError` with a message that is safe to show. Pages use
  `useResource` (loading, error, retry) and `useSubmit` (one submit at a time).
- **Accessibility**: labelled controls via `FormField`, errors linked with `aria-describedby`
  and announced, focus moved to the first invalid field and to each new page's heading, 44 px
  touch targets, AA contrast in light and dark themes, reduced motion respected.
- **Security**: no inline scripts, no `eval`, no `v-html` except the sanitised privacy notice;
  only same-app paths are accepted as post-sign-in redirects; nothing personal is cached by the
  service worker or kept in browser storage (only the language and a parent's chosen ward).
- **Dependencies**: exact versions, each at least 14 days old, including everything in
  `yarn.lock` (`yarn deps:age`).
