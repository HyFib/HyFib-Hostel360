# Linking the institution website to Hostel360

Tender clause 5.1 asks for the hostel portal to be reachable from the institution's own
website. This folder has three ready-made ways to add **Student login**, **Parent login** and
**Staff login** buttons. Pick whichever suits the site; all three link to the same places:

| Button | Goes to | Who uses it |
|---|---|---|
| Student login | `https://<portal>/hostel/login?as=student` | Students (OTP to their registered mobile) |
| Parent login | `https://<portal>/hostel/login?as=parent` | Parents and guardians (OTP) |
| Staff login | `https://<portal>/login` | Wardens, office and mess staff (password + 2FA) |

Replace `https://hostel.accet.ac.in` in the examples with the portal's public address
(the `portal_url` in **Hostel Settings**).

None of the options sets cookies, stores anything in the visitor's browser or sends data
anywhere: they only draw links. Nothing about visitors to the institution site reaches
Hostel360 until they click a button and sign in.

## Option 1: script embed (recommended)

Files: [`hostel360-links.html`](hostel360-links.html), served script
`/assets/hostel360/embed/hostel360-links.js` (from `hostel360/public/embed/`).

Paste the snippet where the buttons should appear:

```html
<div data-hostel360-portal="https://hostel.accet.ac.in"></div>
<script src="https://hostel.accet.ac.in/assets/hostel360/embed/hostel360-links.js" defer></script>
```

The script draws accessible, bilingual (English / தமிழ்) buttons inside a shadow root, so the
site's own CSS cannot break them and they cannot affect the site. Several placeholders on one
page are fine; load the script once.

Optional attributes on the placeholder:

| Attribute | Values | Default |
|---|---|---|
| `data-hostel360-lang` | `both`, `en`, `ta` | `both` (English with Tamil underneath) |
| `data-hostel360-layout` | `row`, `column` | `row` (wraps on phones) |
| `data-hostel360-show` | any of `student,parent,staff` | all three |
| `data-hostel360-target` | `_self`, `_blank` | `_self` |
| `data-hostel360-theme` | `auto`, `light`, `dark` | `auto` (follows the visitor's setting) |

Keep the `<noscript>` links from `hostel360-links.html` inside the placeholder: they show for
visitors without JavaScript and are replaced by the buttons otherwise.

**If the institution site sends a Content-Security-Policy**, add the portal origin to
`script-src` (for example `script-src 'self' https://hostel.accet.ac.in`). No `style-src` or
`connect-src` change is needed.

**Subresource Integrity (optional).** The script changes only when Hostel360 is upgraded. To
pin it, compute the hash after each upgrade and add it to the tag:

```bash
curl -s https://hostel.accet.ac.in/assets/hostel360/embed/hostel360-links.js \
  | openssl dgst -sha384 -binary | openssl base64 -A
```

```html
<script src="https://hostel.accet.ac.in/assets/hostel360/embed/hostel360-links.js"
        integrity="sha384-<hash>" crossorigin="anonymous" defer></script>
```

**Portal web server.** Because the script is loaded by another site, the portal's nginx must
send `Cross-Origin-Resource-Policy: cross-origin` for `/assets/hostel360/embed/` (the rest of
the portal keeps `same-origin`). When SRI with `crossorigin="anonymous"` is used, that location
also needs `Access-Control-Allow-Origin: *`. Both headers are safe there: the folder holds only
this public script.

## Option 2: PHP include

File: [`hostel360-links.php`](hostel360-links.php) (PHP 7.2 or later, no extensions needed).

Copy the file into the site's theme or includes folder, then:

```php
<?php require_once __DIR__ . '/hostel360-links.php'; ?>
<?= hostel360_links('https://hostel.accet.ac.in') ?>
<?= hostel360_links('https://hostel.accet.ac.in', ['lang' => 'ta', 'layout' => 'column', 'show' => ['student', 'parent']]) ?>
```

Options mirror the script (`lang`, `layout`, `show`, `target`) plus `styles` (default `true`;
the small stylesheet is printed once per page, set `false` to style the `.h360-*` classes in the
site's own CSS). Every value written to the page is HTML-escaped and only an `https://` portal
origin is accepted; anything else returns an empty string and logs a PHP warning.

On WordPress, put the file in the active (child) theme and call it from a template, or wrap it
in a small shortcode:

```php
add_shortcode('hostel360_links', function ($atts) {
	require_once get_stylesheet_directory() . '/hostel360-links.php';
	return hostel360_links('https://hostel.accet.ac.in', ['lang' => $atts['lang'] ?? 'both']);
});
```

## Option 3: menu entry

File: [`menu-entry.html`](menu-entry.html).

A plain `<li>` with a sub-menu for the site's main navigation. It uses ordinary links and the
site's existing menu classes (`menu-item`, `sub-menu`, as used by WordPress and most themes),
so it needs no script or stylesheet. In WordPress the same result comes from
**Appearance → Menus → Custom Links** with the three URLs above.

## Hosted entry page (PHP-FPM)

The `public/` and `src/` folders here are a small bilingual entry page served by the
deployment's PHP-FPM pool: `public/index.php` lists the portal links, `public/links.php` returns
the same links as JSON for sites that build their menu from it (cross-origin reads only for the
origins in `H360_WEBSITE_ORIGINS`), and `public/health.php` is its health check. Settings come
from the pool environment (`src/config.php`). The institution can simply link its menu to that
page instead of using the options above.

## Checking the integration

1. Open the institution page on a phone and on a desktop: three buttons (or the chosen subset)
   appear, readable in light and dark mode, and reachable with the Tab key.
2. **Student login** opens the portal's sign-in page titled *Student sign in*; **Parent login**
   shows *Parent sign in*; **Staff login** opens the staff (desk) sign-in page.
3. With JavaScript disabled, the `<noscript>` links appear instead.
4. In the browser's developer tools, the institution page shows no cookies or storage written
   by the script and no network requests other than the script file itself.
