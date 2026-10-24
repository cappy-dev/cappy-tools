# eden-launcher

A small bash script that downloads and launches the latest [Eden](https://git.eden-emu.dev/eden-emu/eden) nightly Linux amd64 PGO AppImage from the official nightly release feed.

Auto-updates by querying `https://nightly.eden-emu.dev/latest/release.json` (the same API Eden itself uses for in-app updates, wired up in `CMakeModules/GenerateSCMRev.cmake`), caches the downloaded AppImage, and only re-downloads when a new nightly tag appears.

## Why

Eden publishes many AppImage variants per nightly (amd64/aarch64/legacy/steamdeck/rog-ally, standard/PGO, plus `.zsync` files). This launcher picks the recommended Linux amd64 PGO build (`Eden-Linux-<commit>-amd64-clang-pgo.AppImage`) and skips the `.zsync` update deltas. It caches under `~/.cache/eden-launcher/` so subsequent launches are instant unless a new release is out.

## Requirements

- `curl`
- `jq`
- A Linux desktop that can run AppImages (FUSE + a display)

Install jq if you don't have it:

```bash
sudo apt install jq     # Debian/Ubuntu
sudo dnf install jq     # Fedora
```

## Usage

```bash
./eden-launcher.sh             # download (if needed) and launch
./eden-launcher.sh --check     # only check for / print the latest version
./eden-launcher.sh --force     # re-download even if cached
./eden-launcher.sh --clean     # delete the cache and exit
./eden-launcher.sh --help      # show help
```

## How it works

1. Fetches `https://nightly.eden-emu.dev/latest/release.json`
2. Reads the `tag_name` and finds the asset whose URL matches `Eden-Linux-[^/]+-amd64-clang-pgo\.AppImage$`
3. Compares the tag to `~/.cache/eden-launcher/current_version`; if they match and the cached AppImage still exists, launches immediately
4. Otherwise downloads to a `.partial` file, atomically renames, marks executable, prunes older AppImages, and launches

## Reference

The update API wiring lives in the Eden source tree:
[`CMakeModules/GenerateSCMRev.cmake`](https://git.eden-emu.dev/eden-emu/eden/src/commit/89004124a55a5ce8c45a90dcb60afd8a107238c3/CMakeModules/GenerateSCMRev.cmake)

Eden is a community emulator project. This launcher is an independent convenience script and is not affiliated with or endorsed by the Eden Emulator Project. All Eden assets remain the property of their respective owners.

## License

AGPL-3.0. See [LICENSE](LICENSE).
