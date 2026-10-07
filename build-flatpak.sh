#!/usr/bin/env bash
# Build Flatkeep as a Flatpak and export a single-file bundle.
#
#   ./build-flatpak.sh            build dist/flatkeep-<arch>.flatpak
#   ./build-flatpak.sh --install  also install it for the current user
#
# The GitHub Actions workflow runs this same script.
set -euo pipefail
cd "$(dirname "$0")"

APP_ID=io.github.cerels.Flatkeep
MANIFEST=$APP_ID.yml
ARCH=$(flatpak --default-arch)
BUNDLE=dist/flatkeep-$ARCH.flatpak
FLATHUB=https://dl.flathub.org/repo/flathub.flatpakrepo

flatpak remote-add --user --if-not-exists flathub "$FLATHUB"

# --install-deps-from downloads the GNOME runtime and SDK the first time.
# rofiles-fuse doesn't work inside CI containers.
flatpak-builder --user --install-deps-from=flathub --disable-rofiles-fuse \
    --force-clean --repo=repo build-dir "$MANIFEST"

# --runtime-repo lets the bundle install its runtime from Flathub if missing.
mkdir -p dist
flatpak build-bundle --runtime-repo="$FLATHUB" repo "$BUNDLE" "$APP_ID"
echo "Built $BUNDLE"

if [[ "${1:-}" == "--install" ]]; then
    flatpak install --user --noninteractive --reinstall --bundle "$BUNDLE"
fi
