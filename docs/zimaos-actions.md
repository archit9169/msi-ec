# Manually build MSI-EC for your ZimaOS kernel

This workflow belongs to `archit9169/msi-ec`. It builds the selected commit of this
fork in Ubuntu on GitHub Actions, then optionally publishes a uniquely tagged
GitHub prerelease. It has only a manual `workflow_dispatch` trigger.

It follows the existing working Docker process: install build tools, download
Linux source, use the host kernel configuration, prepare the kernel tree, build
MSI-EC. GitHub never runs `rmmod`, `insmod`, `make install` or a hardware control.
No privileged container, host networking, host `/lib/modules` mount or idle
`tail -f` process is needed for this cloud build.

The existing fork's Makefile does not use `KERNELDIR`. The helper instead invokes
`make -C TARGET_KERNEL M=MODULE_DIRECTORY modules` and checks the resulting full
vermagic. It must never compile against `uname -r` on a GitHub runner.

## One-time input export, repeated after a kernel update

The existing Files UI folder contains `kernel.config`, `linux-6.18.9.tar.xz`,
`linux-6.18.9/` and `msi-ec/`. Its saved configuration identifies x86_64 Linux
6.18.9, GCC 14.3, `CONFIG_MODULES=y`, and disabled `CONFIG_MODVERSIONS`.
The kernel directory listing did not show `Module.symvers`. This does not mean
the input export or a cloud build has already run.

Open the **ZimaOS host terminal**. First confirm the filesystem path. Historical
output used `/DATA/AppData/msi-ec-build`; the Files UI uses the display name
`ZimaOS-HD/AppData/msi-ec-build`:

```bash
uname -r
ls -ld /DATA/AppData/msi-ec-build
ls -l /DATA/AppData/msi-ec-build/kernel.config \
  /DATA/AppData/msi-ec-build/linux-6.18.9.tar.xz \
  /DATA/AppData/msi-ec-build/msi-ec/msi-ec.ko
```

If that directory is correct, download the export script from this repository,
inspect it, and run it with a **new** output directory:

```bash
curl -fL https://raw.githubusercontent.com/archit9169/msi-ec/main/scripts/ci/export-zimaos-inputs.sh \
  -o /tmp/export-zimaos-inputs.sh
bash /tmp/export-zimaos-inputs.sh \
  /DATA/AppData/msi-ec-build \
  /DATA/AppData/msi-ec-build/github-inputs-6.18.9-01
```

Use the actual verified path if it differs. Run as root if symbol names or host
configuration cannot be read. The script refuses an existing output directory;
use a new suffix on another export. It creates a small
`target-inputs-6.18.9.tar.gz` and prints its SHA256. It does not modify the original
build tree, run builds, load modules, or upload anything.

The export includes the running kernel configuration, release and architecture,
the original kernel source archive's checksum, the tested module's vermagic and
checksum, exported symbol **names and provider names only**, and kernel
`Module.symvers` if present. No kernel addresses, passwords or hardware serials
are included. Review the export before publishing it.

### If an input/tool is missing

- `modinfo`: use the existing Ubuntu build container if it already provides kmod,
  with the build folder visible at its existing mount. Run the script against
  that container path, ensuring `/proc/config.gz` and symbol inventory describe
  the target host. Do not install packages into the ZimaOS system just to export.
- Running configuration: the exporter reads `/proc/config.gz`, otherwise
  `/boot/config-$(uname -r)`. If neither is available, obtain the exact config
  for the current ZimaOS kernel from IceWhale or export it from the host where it
  is exposed. Do not replace it with `make defconfig` or silently trust an old
  config after an OS update. The saved `kernel.config` can be compared against
  a current export, but the script deliberately does not assume it is current.
- Original kernel archive: restore the exact archive used for the working build.
  The workflow downloads kernel.org bytes and checks against this checksum. A
  mismatch stops the build; vendor source/patches require explicit support.
- Reference module: use the existing tested `msi-ec/msi-ec.ko` for the same kernel.
  After a kernel update, obtain vermagic from a native module built for the new
  kernel and adapt the export reference after checking it. Do not force-load an
  old module to obtain metadata.
- Exported symbols: `/proc/kallsyms` must expose `__ksymtab_` names. If names are
  absent, get the exact kernel build outputs instead; do not synthesize symbols.
- `Module.symvers`: use the **kernel build's** file, never the MSI module's own
  `Module.symvers`. If `CONFIG_MODVERSIONS=y`, the exact matching kernel build
  output is mandatory. `modules_prepare` cannot create that file. Obtain it from
  IceWhale's matching build or reproduce their exact kernel source/configuration/
  toolchain build on a build machine. This does not require installing a new kernel.

For this observed non-modversioned configuration, the workflow can build without
`Module.symvers`, preserving the historical approach while adding strict checks:
every undefined module symbol must appear in the host export inventory and must
be built into the kernel. Modular dependencies are rejected in this fallback
because their dependency/namespace metadata cannot be correctly established.
This does not establish full ABI or vendor-patch compatibility. Releases record
the limitation explicitly and remain prereleases awaiting a host load test.

## Make the input bundle accessible to GitHub

GitHub-hosted runners cannot access `zimaos.local`.

1. Download the new input tar.gz from ZimaOS Files to your PC.
2. In this fork, create a release such as `zimaos-inputs-6.18.9-01` and attach the
   input archive. This is an input-assets release, not a built module release.
3. Copy its direct HTTPS asset URL and the SHA256 printed by the exporter.

The workflow currently supports ordinary public GitHub release asset URLs.
Do not use URLs containing tokens. Private inputs would need a separately
configured authenticated download path.

## Run the build yourself

Go to **Actions → Build MSI-EC for ZimaOS → Run workflow**:

- Branch: `main` (or an explicitly selected source revision branch).
- Kernel source version: `6.18.9` for the currently observed setup.
- Target bundle URL: the direct GitHub release asset URL.
- Target bundle SHA256: the full 64-character checksum.
- Publish release: enabled by default; disable for an artifact-only trial.

No repository token secret needs to be created. Compilation has read-only
repository permission; the separate release job gets `contents: write` only
after a successful build. Existing releases are never overwritten. Run ID and
attempt number make each tag unique.

The build checks input hashes, architecture, kernel config, compiler major,
prepared kernel release, ELF architecture, module name, full vermagic and host
symbol availability. A changed kernel configuration outside compiler probes
fails with a diagnostic diff. GCC 14 is provisioned; another compiler major
requires deliberately updating the workflow instead of silently substituting it.
Ubuntu packages can receive updates; exact installed packages and compiler
versions are recorded, but bit-for-bit rebuilds are not promised.

The release contains `msi-ec.ko`, `build.json`, `SHA256SUMS`, `modinfo.txt`,
`packages.txt`, license, release notes and the exact module source archive.
Build logs are retained as a separate Actions artifact, including on failure.

The workflow never installs or tests the module on ZimaOS. Successful compilation
and matching metadata are distinct from successful loading and hardware behavior.
Secure Boot/signing, lockdown, vendor patches and permissions still matter.

## Validation status

Local helper unit tests and syntax/workflow checks are performed during setup.
A genuine GitHub build needs the target input export and a manual workflow run.
No cloud build, module release, host load or control test is implied by installing
these workflow files. Existing upstream COPR/Cloudsmith workflows are unrelated
to this manual kernel-module workflow.

References:
- https://docs.kernel.org/kbuild/modules.html
- https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow

Prepared with OpenAI Codex at the fork owner's request; no upstream contribution
or upstream-maintainer review is implied.
