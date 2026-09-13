# Enabling GitHub Pages — the exact fields

There are two unrelated settings on the same page and it is easy to fill in the
wrong one. Only the first matters.

## 1. The setting you need: Build and deployment → Source

**Settings → Pages → Build and deployment → Source**

Change the dropdown from `Deploy from a branch` to **`GitHub Actions`**.

That is the entire configuration. Nothing is typed anywhere — it is a dropdown.

*(As of the current workflow this should happen on its own: the deploy job runs
`actions/configure-pages` with `enablement: true`, which turns Pages on via the
API. The dropdown is the fallback if that is blocked by an org policy.)*

## 2. The setting to leave EMPTY: Custom domain

The **Custom domain** box is for a domain **you own and control the DNS for** —
something like `tv.example.com` or `yuhana.tv`. It is not for a URL, not for a
path, and not for a `github.io` address.

Valid:      `tv.example.com`
Not valid:  `https://ruman12-code.github.io/YuhanaTV-Streaming/iptv/master.m3u`
Not valid:  `ruman12-code.github.io/YuhanaTV-Streaming`
Not valid:  anything containing `https://`, a slash, or a file name

If a playlist URL was typed in there, GitHub reports:

> The custom domain `…` is not properly formatted.

**To fix it:** clear the box completely and click **Save** (or click **Remove**
if that button is active). Leave it blank. The site is then served from the
default `ruman12-code.github.io` address, which is what every URL in this
repository is already configured for.

## 3. Where the playlist URL actually goes

Not into GitHub at all. It goes into the TV:

**SS IPTV → Settings → Content → External playlists → Add**

```
https://ruman12-code.github.io/YuhanaTV-Streaming/iptv/master.m3u
```

## Checking it worked

* **Settings → Pages** shows *"Your site is live at https://ruman12-code.github.io/YuhanaTV-Streaming/"*.
* **Actions → Update IPTV** → latest run → the `deploy` job is green.
* Opening the URL above in a browser downloads or displays a text file starting
  with `#EXTM3U`.

If the deploy job still fails with *"Get Pages site failed"*, Pages is not yet
set to build from GitHub Actions — go back to step 1.
