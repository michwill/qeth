# qeth Companion — browser extension

Connects dapps in Chrome-family browsers and Firefox to the running qeth
desktop wallet (Frame-compatible JSON-RPC server on `127.0.0.1:1248`), the
same role the Falkon connector plays inside Falkon. One MV3 codebase, no
bundler, no npm.

> **Status:** skeleton (manifest + shared provider + icons). Transport
> (`background.js`/`relay.js`), the status popup, and the build/sign script
> land in the following commits.

## How it works

```
page (MAIN world)          isolated world            extension background        qeth
provider.js ── postMessage ── relay.js ── runtime Port ── background.js ── WS ── :1248
(config.js sets flags/logo)   (per-frame)              (id remap, sub demux,
                                                        __frameOrigin stamp)
```

`provider.js` is shared **byte-for-byte** with
`integrations/falkon/qeth_connector/provider.js`; `config.js` flips it into
WebSocket push mode (no polling, no direct-fetch fallback). The background
holds one WebSocket to `ws://127.0.0.1:1248` — an extension context can open
insecure loopback WS (whitelisted in the manifest CSP `connect-src`), which a
page cannot (mixed content).

## Icons

Rendered from `icons/qeth-icon.svg` (shared with the Falkon connector):

```sh
for n in 16 32 48 128; do
  rsvg-convert -w $n -h $n icons/qeth-icon.svg -o icons/icon${n}.png
  magick icons/icon${n}.png -modulate 100,18 -alpha on \
    -channel A -evaluate multiply 0.85 +channel icons/icon${n}-off.png
done
```

The `-off` (desaturated/dimmed) set is the toolbar icon when qeth is
unreachable; the full-colour set shows once connected.

## Install (development)

Chrome-family: `chrome://extensions` → enable Developer mode → **Load
unpacked** → select this directory.

Firefox: `about:debugging#/runtime/this-firefox` → **Load Temporary Add-on**
→ pick `manifest.json` (temporary; a permanently installable signed `.xpi`
comes from the build script). Firefox host access is user-grantable — see the
popup / `about:addons` → Permissions.
