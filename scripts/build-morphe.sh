#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p build downloads dist tools

log() {
  printf '[build] %s\n' "$*"
}

download_github_asset() {
  local repo="$1"
  local mode="$2"
  local pattern="$3"
  local out="$4"
  local meta="$5"

  python scripts/github_release_asset.py \
    --repo "$repo" \
    --mode "$mode" \
    --asset-regex "$pattern" \
    --out "$out" \
    --meta "$meta"
}

download_app_package() {
  local app_id="$1"
  local package_name="$2"
  local direct_url="$3"
  local apkcombo_app="$4"
  local include_beta="$5"      
  local out="$6"
  local meta="$7"
  local target_version="$8"    

  log "Resolving and downloading ${app_id} package."
  if [[ -n "$direct_url" ]]; then
    rm -rf "$out"
    mkdir -p "$out"
    local url_path="${direct_url%%\?*}"
    local lower_url="${url_path,,}"
    local ext=".apk"
    case "$lower_url" in
      *.apkm) ext=".apkm" ;;
      *.apks) ext=".apks" ;;
      *.xapk) ext=".xapk" ;;
      *.apk) ext=".apk" ;;
    esac
    python scripts/download_file.py --url "$direct_url" --out "$out/manual$ext" --meta "$meta"
    return
  fi

  log "Downloading ${app_id} from APKMirror/APKPure (package=${package_name}, version=${target_version:-latest}, beta=${include_beta})."
  local beta_flag="" ver_flag=""
  [[ "$include_beta" = "true" ]] && beta_flag="--include-beta"
  [[ -n "$target_version" ]] && ver_flag="--target-version $target_version"

  python scripts/apk_downloader.py \
    --name "$app_id" \
    --app "$apkcombo_app" \
    --package "$package_name" \
    --out-dir "$out" \
    --meta "$meta" \
    $beta_flag \
    $ver_flag
}

find_target_version() {
  local package_name="$1"
  local patches_mpp="${2:-tools/patches.mpp}"
  local extra_flags="${3:-}"
  log "Finding target version for ${package_name} from ${patches_mpp} (including experimental)." >&2
  local result
  result="$(python scripts/find_target_version.py --package "$package_name" --patches-mpp "$patches_mpp" --experimental $extra_flags)" || {
    log "WARNING: Could not find target version for ${package_name}" >&2
    return 1
  }
  echo "$result"
}

merge_arm64_package() {
  local app_id="$1"
  local input="$2"
  local out="$3"

  if [[ -f "$input" && "$input" == *.apk ]]; then
    cp "$input" "$out"
    return 0
  fi

  if [[ -d "$input" ]]; then
    local containers=()
    local f
    while IFS= read -r -d '' f; do
      containers+=("$f")
    done < <(find "$input" -maxdepth 1 \( -name '*.apkm' -o -name '*.apks' -o -name '*.xapk' \) -print0)

    if [[ ${#containers[@]} -gt 0 ]]; then
      local extracted_dir="build/${app_id}-container-extracted"
      rm -rf "$extracted_dir"
      mkdir -p "$extracted_dir"
      for f in "${containers[@]}"; do
        log "${app_id}: extracting $(basename "$f") …"
        unzip -o "$f" -d "$extracted_dir" >/dev/null
      done
      input="$extracted_dir"
    fi

    local apk_count
    apk_count="$(find "$input" -maxdepth 1 -name '*.apk' | wc -l)"
    if [[ "$apk_count" -eq 1 ]]; then
      local single
      single="$(find "$input" -maxdepth 1 -name '*.apk' -print -quit)"

      if python -c "
import zipfile, sys
with zipfile.ZipFile(sys.argv[1]) as z:
    apks = [n for n in z.namelist() if n.lower().endswith('.apk')]
    sys.exit(0 if apks else 1)
" "$single" 2>/dev/null; then
        log "${app_id}: single .apk is actually a container, extracting …"
        local container_extracted="build/${app_id}-container-extracted"
        rm -rf "$container_extracted"
        mkdir -p "$container_extracted"
        unzip -o "$single" -d "$container_extracted" >/dev/null
        input="$container_extracted"
        apk_count="$(find "$input" -maxdepth 1 -name '*.apk' | wc -l)"
        if [[ "$apk_count" -eq 1 ]]; then
          single="$(find "$input" -maxdepth 1 -name '*.apk' -print -quit)"
          log "${app_id}: container contained single APK, using directly."
          cp "$single" "$out"
          return 0
        fi
        log "${app_id}: container has ${apk_count} splits, proceeding to merge."
      else
        log "${app_id}: single APK, using directly."
        cp "$single" "$out"
        return 0
      fi
    fi
  fi

  log "Filtering ${app_id} package to arm64-v8a."
  python scripts/filter_splits.py --input "$input" --out-dir "build/${app_id}-arm64"

  log "Merging ${app_id} splits into a standalone APK."
  java -jar tools/APKEditor.jar m -f -i "build/${app_id}-arm64" -o "$out" | tee "build/${app_id}-apkeditor-merge.log"

  test -s "$out"
}

patch_app() {
  local app_id="$1"
  local input="$2"
  local output="$3"
  local extra_patches="${4:-}"
  local disable_default="${5:-}"
  local extra_args=("${@:6}")
  local log_file="build/${app_id}-morphe-patch.log"

  local patches_args=""
  if [[ "$disable_default" != "no-morphe" ]]; then
    patches_args="--patches tools/patches.mpp"
  fi
  [[ -n "$extra_patches" ]] && for p in $extra_patches; do patches_args="$patches_args --patches $p"; done

  log "Applying Morphe patches to ${app_id}."
  set +e
  java -cp "tools/morphe-cli.jar:tools/APKEditor.jar" app.morphe.MorpheLauncherKt patch \
    $patches_args \
    "${KEYSTORE_ARGS[@]}" \
    --temporary-files-path "build/${app_id}-morphe-tmp" \
    --purge \
    --force \
    --continue-on-error \
    --out "$output" \
    "${extra_args[@]}" \
    "$input" | tee "$log_file"
  local patch_status="${PIPESTATUS[0]}"
  set -e

  if [[ "$patch_status" -ne 0 ]]; then
    if grep -qiE 'unknown option|no such option|unrecognized.*out' "$log_file"; then
      log "CLI does not support --out; retrying ${app_id} and discovering generated APK."
      java -cp "tools/morphe-cli.jar:tools/APKEditor.jar" app.morphe.MorpheLauncherKt patch \
        $patches_args \
        "${KEYSTORE_ARGS[@]}" \
        --temporary-files-path "build/${app_id}-morphe-tmp" \
        --purge \
        --force \
        --continue-on-error \
        "${extra_args[@]}" \
        "$input" | tee -a "$log_file"
      local generated
      generated="$(find . -type f -name '*.apk' -newer "$input" | sort | tail -n 1)"
      test -n "$generated"
      cp "$generated" "$output"
    else
      exit "$patch_status"
    fi
  fi

  test -s "$output"
}

log "Resolving latest Morphe CLI release, including prereleases when newer."
if ! download_github_asset "MorpheApp/morphe-cli" "latest-any" '.*\.jar$' tools/morphe-cli.jar build/morphe-cli.json; then
  log "No Morphe CLI jar asset found; building latest release tag from source."
  python scripts/github_release_asset.py --repo "MorpheApp/morphe-cli" --mode "latest-any" --tag-only --meta build/morphe-cli.json
  CLI_TAG="$(python -c 'import json; print(json.load(open("build/morphe-cli.json"))["tag_name"])')"
  git clone --depth 1 --branch "$CLI_TAG" https://github.com/MorpheApp/morphe-cli.git build/morphe-cli-src
  (cd build/morphe-cli-src && ./gradlew --no-daemon cleanShadowJar shadowJar)
  cp build/morphe-cli-src/build/libs/*-all.jar tools/morphe-cli.jar
fi

log "Compiling FindExperimentalVersions helper to access experimental patch targets."
javac -cp tools/morphe-cli.jar scripts/FindExperimentalVersions.java -d build

log "Resolving latest Morphe patches release."
if ! download_github_asset "MorpheApp/morphe-patches" "latest-any" '.*\.mpp$' tools/patches.mpp build/morphe-patches.json; then
  log "No Morphe patch bundle asset found; building latest release tag from source."
  python scripts/github_release_asset.py --repo "MorpheApp/morphe-patches" --mode "latest-any" --tag-only --meta build/morphe-patches.json
  PATCH_TAG="$(python -c 'import json; print(json.load(open("build/morphe-patches.json"))["tag_name"])')"
  git clone --depth 1 --branch "$PATCH_TAG" https://github.com/MorpheApp/morphe-patches.git build/morphe-patches-src
  (cd build/morphe-patches-src && ./gradlew --no-daemon cleanJar jar)
  cp build/morphe-patches-src/patches/build/libs/*.mpp tools/patches.mpp
fi

log "Resolving latest Piko patches release for X."
if ! download_github_asset "crimera/piko" "latest-any" '.*\.mpp$' tools/piko-patches.mpp build/piko-patches.json; then
  log "No Piko patch bundle asset found; building latest release tag from source."
  python scripts/github_release_asset.py --repo "crimera/piko" --mode "latest-any" --tag-only --meta build/piko-patches.json
  PIKO_TAG="$(python -c 'import json; print(json.load(open("build/piko-patches.json"))["tag_name"])')"
  git clone --depth 1 --branch "$PIKO_TAG" https://github.com/crimera/piko.git build/piko-src
  (cd build/piko-src && ./gradlew --no-daemon cleanJar jar)
  cp build/piko-src/patches/build/libs/*.mpp tools/piko-patches.mpp
fi

log "Downloading APKEditor for split APK/APKM merging."
download_github_asset "REAndroid/APKEditor" "latest-stable" 'APKEditor.*\.jar$' tools/APKEditor.jar build/apkeditor.json

if [[ -n "${SIGNING_KEY:-}" ]]; then
  log "Using signing key from GitHub Secrets."
  echo "$SIGNING_KEY" | base64 -d > build/release-source.keystore
  log "Converting user keystore to BKS format …"

  _KS_ALIAS="${ALIAS:-Morphe}"

  keytool -importkeystore \
    -srckeystore build/release-source.keystore \
    -destkeystore build/release.p12 \
    -deststoretype PKCS12 \
    -srcalias "$_KS_ALIAS" \
    -destalias "$_KS_ALIAS" \
    -srcstorepass "${KEYSTORE_PASSWORD:-}" \
    -srckeypass "${KEY_PASSWORD:-}" \
    -deststorepass "${KEYSTORE_PASSWORD:-}" \
    -destkeypass "${KEY_PASSWORD:-}" \
    -noprompt 2>/dev/null

  keytool -importkeystore \
    -srckeystore build/release.p12 \
    -srcstoretype PKCS12 \
    -destkeystore build/release.keystore \
    -deststoretype BKS \
    -srcalias "$_KS_ALIAS" \
    -destalias "$_KS_ALIAS" \
    -srcstorepass "${KEYSTORE_PASSWORD:-}" \
    -srckeypass "${KEY_PASSWORD:-}" \
    -deststorepass "${KEYSTORE_PASSWORD:-}" \
    -destkeypass "${KEY_PASSWORD:-}" \
    -providerclass org.bouncycastle.jce.provider.BouncyCastleProvider \
    -providerpath tools/morphe-cli.jar \
    -noprompt 2>/dev/null

  rm -f build/release-source.keystore build/release.p12
  KEYSTORE_ARGS=(
    --keystore build/release.keystore
    --keystore-password "${KEYSTORE_PASSWORD:-}"
    --keystore-entry-alias "${ALIAS:-Morphe}"
    --keystore-entry-password "${KEY_PASSWORD:-}"
  )
else
  KEYSTORE_ARGS=(--keystore build/morphe.keystore)
fi

YOUTUBE_OK=false
YOUTUBE_TARGET="$(find_target_version "$PACKAGE_NAME")" || true
if download_app_package "youtube" "$PACKAGE_NAME" "${YOUTUBE_URL:-}" "${APKCOMBO_YOUTUBE_APP:-youtube}" "${APKCOMBO_YOUTUBE_BETA:-true}" downloads/youtube-play build/youtube.json "$YOUTUBE_TARGET" && \
   merge_arm64_package "youtube" downloads/youtube-play build/youtube-arm64-merged.apk; then
  log "Stripping native libraries from YouTube APK before patching (preserves V2/V3 signing)."
  cp build/youtube-arm64-merged.apk build/youtube-arm64-nolibs.apk
  zip -d build/youtube-arm64-nolibs.apk "lib/*" >/dev/null 2>&1 || true
  if patch_app "youtube" build/youtube-arm64-nolibs.apk "build/youtube-morphe-arm64.apk" "" "" \
       -d "Change header" -d "Custom branding" -d "GmsCore support" \
       -e "Change package name" -OpackageName=com.google.android.youtube; then
    YOUTUBE_OK=true
  fi
fi
[ "$YOUTUBE_OK" != true ] && log "WARNING: YouTube build failed, excluding from release"

REDDIT_OK=false
REDDIT_TARGET="$(find_target_version "${REDDIT_PACKAGE_NAME:-com.reddit.frontpage}" || true)"
if download_app_package "reddit" "${REDDIT_PACKAGE_NAME:-com.reddit.frontpage}" "${REDDIT_URL:-}" "${APKCOMBO_REDDIT_APP:-reddit}" "${APKCOMBO_REDDIT_BETA:-false}" downloads/reddit-play build/reddit.json "$REDDIT_TARGET" && \
   merge_arm64_package "reddit" downloads/reddit-play build/reddit-arm64-merged.apk && \
   patch_app "reddit" build/reddit-arm64-merged.apk "dist/reddit.apk"; then
  REDDIT_OK=true
else
  log "WARNING: Reddit build failed, excluding from release"
fi

TWITTER_OK=false
TWITTER_TARGET="$(find_target_version "${TWITTER_PACKAGE_NAME:-com.twitter.android}" tools/piko-patches.mpp "--no-beta" || true)"
if download_app_package "twitter" "${TWITTER_PACKAGE_NAME:-com.twitter.android}" "${TWITTER_URL:-}" "${APKCOMBO_TWITTER_APP:-twitter}" "${APKCOMBO_TWITTER_BETA:-true}" downloads/twitter-play build/twitter.json "$TWITTER_TARGET" && \
   merge_arm64_package "twitter" downloads/twitter-play build/twitter-arm64-merged.apk && \
   patch_app "twitter" build/twitter-arm64-merged.apk "dist/twitter.apk" "tools/piko-patches.mpp" "no-morphe" -d "Change app icon"; then
  TWITTER_OK=true
else
  log "WARNING: X build failed, excluding from release"
fi

if [ "$YOUTUBE_OK" = true ]; then
  log "Packaging KernelSU/Magisk-compatible module."
  MODULE_VERSION_CODE="$(TZ=Asia/Shanghai date +'%Y%m%d%H%M')"
  python scripts/make_ksu_module.py \
    --apk "build/youtube-morphe-arm64.apk" \
    --stock-apk build/youtube-arm64-merged.apk \
    --module-id "youtube_morphe" \
    --module-name "Youtube Morphe" \
    --out-dir dist \
    --metadata build/youtube.json \
    --patches-metadata build/morphe-patches.json \
    --cli-metadata build/morphe-cli.json \
    --bin-dir tools/bin \
    --version-code "$MODULE_VERSION_CODE" \
    --repo "${GITHUB_REPOSITORY:-}"
fi

get_json_field() {
  local file="$1" field="$2"
  python -c "
import json, sys
d = json.load(open('$file'))
print(d.get('$field') or d.get('version') or d.get('title') or 'unknown')
"
}

PATCHES_VER="$(get_json_field build/morphe-patches.json tag_name)"
PIKO_VER="$(get_json_field build/piko-patches.json    tag_name)"
CLI_VER="$(get_json_field  build/morphe-cli.json      tag_name)"

[ "$YOUTUBE_OK" = true ] && YT_VER="$(get_json_field build/youtube.json version)"
[ "$REDDIT_OK"  = true ] && RD_VER="$(get_json_field build/reddit.json  version)"
[ "$TWITTER_OK" = true ] && TW_VER="$(get_json_field build/twitter.json version)"

VERSION_SUMMARY_ARGS="--patches build/morphe-patches.json --piko-patches build/piko-patches.json"
[ "$YOUTUBE_OK" = true ] && VERSION_SUMMARY_ARGS+=" --youtube build/youtube.json"
[ "$REDDIT_OK"  = true ] && VERSION_SUMMARY_ARGS+=" --reddit  build/reddit.json"
[ "$TWITTER_OK" = true ] && VERSION_SUMMARY_ARGS+=" --twitter build/twitter.json"
VERSION="$(python scripts/version_summary.py $VERSION_SUMMARY_ARGS)"


APP_ENTRIES=(
  "$YOUTUBE_OK|YouTube|${YT_VER:-}|dist/youtube.zip|dist/YouTube-v${YT_VER:-}.zip"
  "$REDDIT_OK|Reddit|${RD_VER:-}|dist/reddit.apk|dist/Reddit-v${RD_VER:-}.apk"
  "$TWITTER_OK|X / Twitter|${TW_VER:-}|dist/twitter.apk|dist/X-v${TW_VER:-}.apk"
)

RELEASE_FILES=()
for entry in "${APP_ENTRIES[@]}"; do
  IFS='|' read -r ok _name _ver src dest <<< "$entry"
  [ "$ok" != true ] && continue
  [ -f "$src" ] && mv "$src" "$dest"
  RELEASE_FILES+=("$dest")
done

printf '%s\n' "${RELEASE_FILES[@]}" > build/release-files.txt

NOW="$(TZ=Asia/Shanghai date +'%Y-%m-%d %H:%M')"
RELEASE_TAG="${PATCHES_VER}"
RELEASE_NAME="Morphe Builds - $NOW"

{
  echo "RELEASE_TAG=$RELEASE_TAG"
  echo "RELEASE_NAME=$RELEASE_NAME"
} >> "$GITHUB_ENV"


{
  echo "# Morphe Builds — ${PATCHES_VER}"
  echo ""
  echo "## Available Files"
  echo ""

  echo "### YouTube"
  if [ "$YOUTUBE_OK" = true ]; then
    echo "- universal: \`${YT_VER}\`"
  else
    echo "- *build failed*"
  fi
  echo ""

  echo "### Reddit"
  if [ "$REDDIT_OK" = true ]; then
    echo "- arm64-v8a: \`${RD_VER}\`"
  else
    echo "- *build failed*"
  fi
  echo ""

  echo "### X"
  if [ "$TWITTER_OK" = true ]; then
    echo "- arm64-v8a: \`${TW_VER}\`"
  else
    echo "- *build failed*"
  fi
  echo ""

  echo "## Patch Sources"
  echo ""
  echo "### Morphe Patches"
  echo "- tag: \`${PATCHES_VER}\`"
  echo ""
  echo "### Piko Patches"
  echo "- tag: \`${PIKO_VER}\`"
  echo ""
  echo "### Morphe CLI"
  echo "- version: \`${CLI_VER}\`"

} > build/release-notes.md

if [ "$YOUTUBE_OK" = true ]; then
  {
    echo "# YouTube Morphe"
    echo ""
    echo "**Patched Version:** \`${YT_VER}\`"
    echo "**Build Date:** ${NOW}"
    echo ""
    echo "## Components"
    echo ""
    echo "| Component | Version |"
    echo "| :--- | :--- |"
    echo "| Morphe Patches | \`${PATCHES_VER}\` |"
    echo "| Morphe CLI | \`${CLI_VER}\` |"
  } > dist/YouTube-changelog.md
fi

log "Done: $RELEASE_TAG"
