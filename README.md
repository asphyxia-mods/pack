# asphyxia mods

Fabric modpack, managed with [packwiz](https://packwiz.infra.link/). Git holds
the desired state (which mods, exact versions, hashes, configs); CI builds the
installable `.mrpack`.

```
packs/26.3/          Minecraft 26.3 pack (packwiz)
  pack.toml          pack version, Minecraft + Fabric versions
  mods/*.pw.toml     one file per mod: download URL + hash + update source
  forks.toml         which GitHub release assets the pack's forks/ports provide
  owners.toml        which mod each config/other file belongs to
  config/ ...        configs and other files shipped as-is
scripts/
  import-mrpack.py   one-time import of an existing .mrpack
  update-forks.py    pin/update mods built from GitHub releases
  validate-pack.py   hashes, duplicate mod ids, missing deps, breaks/conflicts
  check-files.py     every config belongs to a mod still in the pack
  install-mrpack.py  install a pack's client side into a game directory
  embed-external.py  move non-Modrinth downloads into a .mrpack as overrides
  client-test.py     boot the real client and join a world
  report.py          markdown diff between two exported packs
```

## Where mods come from

| Source | How it's pinned | Updated by |
| --- | --- | --- |
| Modrinth | `[update.modrinth]` in `.pw.toml` | `packwiz update` |
| Forks/ports (CustomHUD, Inventory Tabs, TransformHandlers, asphyxia-core) | release URL + sha256 | `scripts/update-forks.py` |

packwiz's built-in GitHub updater always takes the newest release, which breaks
for repos that publish builds for several Minecraft versions. That's why forks
use `forks.toml` instead: each entry has a regex for the asset names that belong
to this pack (e.g. `\+26\.3\.jar$`), and the newest matching release wins.

## Common tasks

Install packwiz (CI pins the same commit):

```sh
go install github.com/packwiz/packwiz@170b22d78595
```

All commands below run inside `packs/26.3/`.

```sh
packwiz mr add sodium                    # add a Modrinth mod (+ its dependencies)
packwiz remove sodium && python3 ../../scripts/check-files.py . --prune   # remove a mod + its configs
packwiz update --all                     # update Modrinth mods
packwiz pin sodium                       # stop a mod from being updated
python3 ../../scripts/update-forks.py . --check              # list fork updates
python3 ../../scripts/update-forks.py .                      # apply them
python3 ../../scripts/update-forks.py . --set custom-hud=v4.1.2.2+26.3   # pin/roll back
packwiz refresh                          # after hand edits to files; CI checks this
packwiz mr export                        # build the .mrpack
python3 ../../scripts/validate-pack.py *.mrpack
```

Adding a config: drop it in place and add a line to `owners.toml` naming the
mod (`mods/<name>.pw.toml`) it belongs to. CI fails on files with no owner, on
files whose mod was removed, and on rules that no longer match anything.

Adding a new fork: add a `[[fork]]` entry to `forks.toml` (with
`prerelease = true` if its builds are published as GitHub prereleases), then run
`update-forks.py . --set NAME=TAG` (or omit `--set` for the newest build) and
`packwiz refresh`.

## CI

- **Verify** (push / PR): index is up to date, every file has an owner, pack
  exports, validation passes, and the client launches on both graphics
  backends.
- **Nightly** (03:23 UTC): updates everything on a scratch checkout, exports a
  candidate, validates and launch-tests it, and uploads the `.mrpack`,
  `report.md` and `changes.patch` as an artifact. It never commits. To accept a nightly's
  updates, `git apply changes.patch` from the repo root and commit.
- **Release** (manual): builds `packs/<pack>` as release `v<version>+<pack>`
  using the version in `pack.toml`, see below.

### Releases

Run the **Release** workflow and pick a pack. It refuses to run if the tag
already exists, so bump `version` in `pack.toml` first. The workflow:

1. exports the pack and embeds every non-Modrinth file (the fork builds) into
   the `.mrpack` as overrides, so launchers like Prism don't warn about
   downloads from outside Modrinth;
2. validates it and runs the client launch test on both graphics backends
   against that exact file;
3. writes release notes comparing it with the previous release of the pack;
4. attests the `.mrpack`'s build provenance and publishes it with
   `SHA256SUMS`. The release is created as a draft with all assets, then
   published, after which it is immutable.

Verify a downloaded pack with:

```sh
gh attestation verify asphyxia-mods-26.3-<version>.mrpack -R asphyxia-mods/pack
```

### Client launch test

`scripts/client-test.py` installs the exported pack, starts a vanilla server
(offline mode, flat world), launches the Fabric client with
[portablemc](https://github.com/mindstorm38/portablemc) and joins it. It
passes when the player joins and the client stays in-game for 45 seconds
without crashing. It runs once per graphics backend (`--backend opengl` /
`vulkan`) and fails if the game falls back to the other one. Rendering uses
Mesa's software drivers (llvmpipe / lavapipe) under Xvfb. Logs, crash reports
and a screenshot are uploaded as artifacts.

Running it locally needs Java 25, Xvfb and portablemc:

```sh
xvfb-run -a -s "-screen 0 1280x720x24" \
  python3 scripts/client-test.py pack.mrpack --backend opengl
```
