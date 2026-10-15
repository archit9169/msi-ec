#!/usr/bin/env bash
# Run on the target ZimaOS host. Reads kernel/build inputs; creates one NEW export.
# Never builds, installs, loads, unloads, or changes a hardware setting.
set -euo pipefail
build_dir=${1:?Usage: bash export-zimaos-inputs.sh BUILD_DIRECTORY NEW_OUTPUT_DIRECTORY}
output=${2:?Choose a new output directory; existing directories are refused}
[[ -d "$build_dir" ]] || { echo 'Build directory does not exist' >&2; exit 1; }
[[ ! -e "$output" ]] || { echo 'Output directory already exists; choose a new name' >&2; exit 1; }
for command in awk sort sha256sum tar gzip uname modinfo; do
  command -v "$command" >/dev/null || { echo "Missing tool: $command" >&2; exit 1; }
done
release=$(uname -r)
[[ "$release" =~ ^[0-9]+\.[0-9]+\.[0-9]+[-a-zA-Z0-9._+]*$ ]] || exit 1
version=${release%%-*}
version=${version%%+*}
source_archive="$build_dir/linux-$version.tar.xz"
[[ -f "$source_archive" ]] || { echo "Missing original source archive: $source_archive" >&2; exit 1; }
mkdir -p "$output/inputs"
inputs="$output/inputs"
if [[ -r /proc/config.gz ]]; then
  gzip -dc /proc/config.gz > "$inputs/kernel.config"
  printf '%s\n' 'running kernel /proc/config.gz' > "$inputs/config-origin.txt"
elif [[ -r "/boot/config-$release" ]]; then
  cp "/boot/config-$release" "$inputs/kernel.config"
  printf '%s\n' "host /boot/config-$release" > "$inputs/config-origin.txt"
else
  echo 'Cannot obtain running-kernel configuration. Do not substitute a newly generated default config.' >&2
  echo 'Export /proc/config.gz from the ZimaOS host, or obtain the exact host kernel config from IceWhale.' >&2
  exit 1
fi
printf '%s\n' "$release" > "$inputs/kernel-release.txt"
printf '%s\n' "$version" > "$inputs/source-version.txt"
uname -m > "$inputs/architecture.txt"
sha256sum "$source_archive" | awk '{print $1}' > "$inputs/kernel-source-sha256.txt"

# Keep symbol NAMES only, never kernel addresses. A loaded provider is marked so
# the builder can reject dependencies not already built into the host kernel.
awk '$3 ~ /^__ksymtab_/ {sub(/^__ksymtab_/, "", $3); print $3, (NF >= 4 ? $4 : "builtin")}' \
  /proc/kallsyms | LC_ALL=C sort -u > "$inputs/exported-symbols.txt"
[[ -s "$inputs/exported-symbols.txt" ]] || {
  echo 'No exported symbol names visible. Run as root on the target host; no settings are changed.' >&2; exit 1;
}

# The previously tested module is only a vermagic reference, never loaded here.
reference="$build_dir/msi-ec/msi-ec.ko"
[[ -f "$reference" ]] || { echo "Missing tested reference module: $reference" >&2; exit 1; }
modinfo -F vermagic "$reference" > "$inputs/vermagic.txt"
[[ "$(awk '{print $1}' "$inputs/vermagic.txt")" == "$release" ]] || {
  echo 'Reference module is for a different kernel; use a native module from the running kernel as the reference.' >&2; exit 1;
}
sha256sum "$reference" | awk '{print $1}' > "$inputs/reference-module-sha256.txt"

# Only copy genuine kernel-build Module.symvers, never the MSI module's own file.
if [[ -s "$build_dir/linux-$version/Module.symvers" ]]; then
  cp "$build_dir/linux-$version/Module.symvers" "$inputs/Module.symvers"
fi
if grep -qx 'CONFIG_MODVERSIONS=y' "$inputs/kernel.config" && [[ ! -s "$inputs/Module.symvers" ]]; then
  echo 'CONFIG_MODVERSIONS=y requires Module.symvers from the exact host kernel build.' >&2
  echo 'Obtain the matching IceWhale build outputs. modules_prepare cannot generate it.' >&2
  exit 1
fi
(cd "$inputs" && sha256sum ./* > ../INPUT-SHA256SUMS)
tar -czf "$output/target-inputs-$release.tar.gz" -C "$inputs" .
sha256sum "$output/target-inputs-$release.tar.gz"
echo 'Export complete. Inspect the files, then attach the tar.gz to a GitHub input-assets release.'
echo 'No compilation, module loading, hardware writes, or uploads were performed.'
