# qeth AppImage (sketch)

A portable single-file build, complementary to the Flatpak in `../flatpak/`.
**Status: scaffolding — structure is right, but it has not been run end-to-end
yet; the system-lib list and theming need iteration on a real target (the VM).**

## Why a container (the whole idea)

An AppImage is only as portable as the *oldest* environment it was assembled
in, on two axes that are really the same problem — "target the lowest common
denominator":

| | Host (Gentoo) | What the bundle needs |
|---|---|---|
| glibc | 2.42 (bleeding edge) | as **old** as possible — built against 2.X runs on ≥2.X |
| CPU | `-march=native` (AVX-512…) | **generic** x86-64 baseline (SSE2) — runs on any 64-bit x86 |

Building on the host would bake in glibc 2.42 *and* native instructions →
`GLIBC_2.4x not found` / `SIGILL` almost everywhere (not even the test VM at
glibc 2.39). So we assemble **inside `quay.io/pypa/manylinux_2_34`**: its
interpreter, the PyPI manylinux wheels (`[bundled]` PySide6 + the eth stack),
and the bundled system libs are all old-glibc + generic. **Nothing from the
host venv is ever copied in** — `build-appimage.sh` does a fresh `pip install`.
This is the same hermeticity the Flatpak gets from `org.kde.Platform`.

## glibc floor ↔ PySide6 version (the one lever)

PySide6's own wheel sets the floor, and 6.10 raised it:

| PySide6 | container | glibc floor | reaches |
|---|---|---|---|
| **≤ 6.9** | `manylinux_2_28` | **2.28** | Debian 10+, RHEL 8+, Ubuntu 20.04+ (≈2019) |
| **6.10 / 6.11** (default) | `manylinux_2_34` | **2.34** | Ubuntu 22.04+, Debian 12+, RHEL 9+, Fedora 35+ (≈2022) |

You can't go below 2.28 — PySide6 ships no older wheel. For maximum reach pin
`PySide6<6.10` and use `manylinux_2_28` (see the `IMAGE` knob below).

## Build

```bash
# Local (needs podman or docker):
./dist/appimage/build-in-container.sh            # -> dist/appimage/out/qeth-<ver>-x86_64.AppImage
IMAGE=quay.io/pypa/manylinux_2_28_x86_64 ./dist/appimage/build-in-container.sh   # glibc 2.28

# CI (recommended; no local container needed): .github/workflows/appimage.yml
# builds it on every v* tag and attaches it to the GitHub release next to the
# .flatpak.
```

## Files

- `build-appimage.sh` — runs **inside** the container; assembles the AppDir + packs it.
- `build-in-container.sh` — host wrapper: `docker/podman run` the above.
- `AppRun` — entrypoint; sets `PYTHONHOME`/`LD_LIBRARY_PATH`, launches `python -m qeth`.
- `io.github.michwill.qeth.desktop` — top-level desktop entry.
- (app side) `qeth/__main__.py` `_running_bundled_qt()` now also fires on
  `APPIMAGE`, so the host font/icon adoption runs here too.

## Open items (need a real run + the VM)

1. **xcb system libs** — the `dnf install` list in `build-appimage.sh` is a
   first guess. Launch on a clean target and chase whatever the platform plugin
   reports missing (`QT_DEBUG_PLUGINS=1`). `libxcb-cursor` is the classic.
2. **Theming fidelity** — font adoption is wired; icon theme currently uses the
   legible-default probe. Since an AppImage is *un*sandboxed, it can read the
   user's real qt6ct `icon_theme`/Kvantum off disk — honouring those exactly is
   the make-or-break visual test (see the `TODO(appimage)` in `__main__.py`).
3. **Size** — `[bundled]` pulls all of PySide6 (~150 MB of Qt). Trim unused Qt
   modules before shipping.
4. **CI gotchas** — `appimagetool` runs with `--appimage-extract-and-run` (no
   FUSE in the container); revisit if the runtime download or packaging fails.

## Tradeoff vs the Flatpak

The Flatpak gets a fixed glibc + Ledger USB via its runtime/portals for free;
the AppImage trades that for a no-install single file and direct (un-sandboxed)
access to USB + the host theme. The container build is what keeps the AppImage's
libc and CPU baseline honest — the one place it's more work than the Flatpak.
