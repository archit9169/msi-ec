#!/usr/bin/env python3
"""Build only: no module installation/loading and no access to a ZimaOS host."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import urllib.parse


INPUT_FILES = {
    'kernel.config', 'config-origin.txt', 'kernel-release.txt', 'source-version.txt',
    'architecture.txt', 'kernel-source-sha256.txt', 'exported-symbols.txt',
    'vermagic.txt', 'reference-module-sha256.txt', 'Module.symvers',
}


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def valid_version(value):
    require(re.fullmatch(r'[1-9]\d*\.\d+\.\d+', value), 'Use a numeric kernel.org source version, e.g. 6.18.9')
    return value


def valid_hash(value):
    require(re.fullmatch(r'[0-9a-fA-F]{64}', value), 'Expected a full SHA256')
    return value.lower()


def extract_inputs(archive, destination):
    """Accept a small flat bundle, never links, duplicate names or special files."""
    destination.mkdir(parents=True, exist_ok=False)
    seen = set()
    total = 0
    with tarfile.open(archive) as tar:
        for member in tar:
            if member.isdir() and member.name in ('.', './'):
                continue
            name = member.name.removeprefix('./')
            require(member.isfile() and name in INPUT_FILES, f'Unexpected input entry: {member.name}')
            require(name not in seen, f'Duplicate input: {name}')
            total += member.size
            require(0 <= member.size <= 16 * 1024 * 1024 and total <= 32 * 1024 * 1024,
                    'Target input bundle is too large')
            seen.add(name)
            with tar.extractfile(member) as source, (destination / name).open('wb') as output:
                shutil.copyfileobj(source, output)
    require(INPUT_FILES - {'Module.symvers'} <= seen, 'Target input bundle is incomplete')


def config_values(text):
    result = {}
    for line in text.splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            key, value = line.split('=', 1)
            result[key] = value
        elif re.fullmatch(r'# CONFIG_\w+ is not set', line):
            result[line.split()[1]] = 'n'
    return result


def config_changes(before, after):
    # Kconfig regenerates these compiler/tool probes on a different build machine.
    # All other changes require investigation instead of silently changing the host config.
    prefixes = ('CONFIG_CC_', 'CONFIG_GCC_VERSION', 'CONFIG_CLANG_VERSION', 'CONFIG_AS_',
                'CONFIG_LD_', 'CONFIG_LLD_VERSION', 'CONFIG_RUSTC_', 'CONFIG_PAHOLE_VERSION',
                'CONFIG_TOOLS_SUPPORT_')
    return {key: [before.get(key, 'n'), after.get(key, 'n')]
            for key in before.keys() | after.keys()
            if before.get(key, 'n') != after.get(key, 'n') and not key.startswith(prefixes)}


def symbol_inventory(text):
    result = {}
    for line in text.splitlines():
        parts = line.split()
        require(len(parts) == 2 and re.fullmatch(r'[A-Za-z0-9_.$]+', parts[0]), 'Invalid exported-symbol inventory')
        require(parts[0] not in result or result[parts[0]] == parts[1], 'Ambiguous symbol provider')
        result[parts[0]] = parts[1]
    require(result, 'Empty exported-symbol inventory')
    return result


def verify_symbols(undefined, exports, has_symvers):
    missing = sorted(set(undefined) - exports.keys())
    require(not missing, 'Module references symbols not exported by target host: ' + ', '.join(missing))
    # Without genuine Module.symvers, modpost cannot correctly derive module dependencies
    # or namespaces. Restrict this fallback to symbols already built into the host.
    modular = sorted(s for s in undefined if exports[s] != 'builtin')
    require(has_symvers or not modular,
            'Module.symvers required for dependencies supplied by other modules: ' + ', '.join(modular))


def main():
    root = Path.cwd()
    work = root / 'build-zimaos'
    logs = work / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    dist = root / 'dist-zimaos'
    require(not dist.exists(), 'Refusing to reuse an existing release directory')
    serial = 0

    def run(args, cwd=root):
        nonlocal serial
        serial += 1
        path = logs / f'{serial:02d}-{Path(str(args[0])).name}.log'
        print('+', ' '.join(map(str, args)), flush=True)
        with path.open('w', encoding='utf-8') as log:
            result = subprocess.run(list(map(str, args)), cwd=cwd, stdout=log, stderr=subprocess.STDOUT, timeout=2400)
        text = path.read_text(encoding='utf-8', errors='replace')
        require(result.returncode == 0, f'Command failed; see {path.relative_to(root)}:\n{text[-6000:]}')
        return text.strip()

    version = valid_version(os.environ['TARGET_KERNEL_VERSION'])
    expected_bundle_hash = valid_hash(os.environ['TARGET_BUNDLE_SHA256'])
    url = os.environ['TARGET_BUNDLE_URL']
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == 'https' and parsed.hostname == 'github.com' and
            '/releases/download/' in parsed.path and not parsed.username and not parsed.query,
            'Use an ordinary public GitHub release asset URL, without credentials or query strings')
    bundle = work / 'target-inputs.tar.gz'
    run(['curl', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
         '--retry', '3', '--max-time', '120', '--max-filesize', str(32 * 1024 * 1024),
         '--output', bundle, url])
    require(sha256(bundle) == expected_bundle_hash, 'Target bundle SHA256 mismatch')
    target = work / 'target'
    extract_inputs(bundle, target)
    read = lambda name: (target / name).read_text().strip()
    release = read('kernel-release.txt')
    require(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+[-a-zA-Z0-9._+]*', release), 'Invalid target release')
    require(read('source-version.txt') == version, 'Target export does not match selected source version')
    require(read('architecture.txt') == 'x86_64', 'This workflow supports x86_64 only')
    config = config_values(read('kernel.config'))
    require(config.get('CONFIG_X86_64') == 'y' and config.get('CONFIG_MODULES') == 'y', 'Host config must enable x86_64 modules')
    require(config.get('CONFIG_CC_IS_GCC') == 'y', 'This workflow requires a GCC-built target')
    require(config.get('CONFIG_MODULE_SIG_FORCE', 'n') != 'y', 'Target requires trusted module signatures; configure a separate signing process first')
    gcc = os.environ.get('CC', 'gcc-14')
    compiler = run([gcc, '-dumpfullversion'])
    require(int(config['CONFIG_GCC_VERSION']) // 10000 == int(compiler.split('.')[0]), 'Compiler major version does not match target')
    require(config.get('CONFIG_RANDSTRUCT_NONE', 'n') == 'y', 'Randomized layout requires the original kernel build seed/toolchain')
    exports = symbol_inventory(read('exported-symbols.txt'))
    has_symvers = (target / 'Module.symvers').is_file() and (target / 'Module.symvers').stat().st_size > 0
    require(config.get('CONFIG_MODVERSIONS', 'n') != 'y' or has_symvers,
            'CONFIG_MODVERSIONS requires genuine Module.symvers from the exact target kernel build')
    source_hash = valid_hash(read('kernel-source-sha256.txt'))
    source_url = f'https://cdn.kernel.org/pub/linux/kernel/v{version.split(".")[0]}.x/linux-{version}.tar.xz'
    archive = work / f'linux-{version}.tar.xz'
    run(['curl', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
         '--retry', '3', '--max-time', '600', '--output', archive, source_url])
    require(sha256(archive) == source_hash, 'Kernel.org source differs from exported source archive; resolve exact vendor source/patches first')
    with tarfile.open(archive) as tar:
        require(all(m.name.startswith(f'linux-{version}/') or m.name == f'linux-{version}' for m in tar), 'Unexpected kernel archive root')
        tar.extractall(work, filter='data')
    kernel = work / f'linux-{version}'
    shutil.copyfile(target / 'kernel.config', kernel / '.config')
    make = ['make', '-C', kernel, 'ARCH=x86', f'CC={gcc}', f'HOSTCC={os.environ.get("HOSTCC", gcc)}']
    run(make + ['olddefconfig'])
    prepared = config_values((kernel / '.config').read_text())
    changes = config_changes(config, prepared)
    (logs / 'configuration-changes.json').write_text(json.dumps(changes, indent=2) + '\n')
    require(not changes, 'Kernel configuration changed beyond compiler probes; see configuration-changes.json')
    actual_release = run(make + ['-s', '--no-print-directory', 'kernelrelease']).splitlines()[-1]
    require(actual_release == release, f'Prepared kernel release {actual_release!r} does not match target {release!r}')
    run(make + ['-j2', 'modules_prepare'])
    if has_symvers:
        shutil.copyfile(target / 'Module.symvers', kernel / 'Module.symvers')
    module_dir = work / 'module'
    module_dir.mkdir()
    for name in ('Makefile', 'Makefile.vars', 'msi-ec.c', 'ec_memory_configuration.h'):
        shutil.copyfile(root / name, module_dir / name)
    arguments = make + ['-j2', f'M={module_dir}', f'KERNELRELEASE={release}', 'modules']
    if not has_symvers:
        # Reproduce the working non-modversioned host build, with a mandatory
        # exported-symbol audit below. This is explicitly reported as limited validation.
        arguments.append('KBUILD_MODPOST_WARN=1')
    run(arguments)
    module = module_dir / 'msi-ec.ko'
    elf = module.read_bytes()
    require(elf[:6] == b'\x7fELF\x02\x01' and int.from_bytes(elf[18:20], 'little') == 62,
            'Expected a 64-bit little-endian x86_64 ELF module')
    name = run(['modinfo', '-F', 'name', module])
    require(name.replace('-', '_') == 'msi_ec', 'Wrong module name')
    vermagic = run(['modinfo', '-F', 'vermagic', module])
    require(vermagic.split() == read('vermagic.txt').split(), 'Full vermagic differs from the target reference module')
    symbols = [line.split()[-1] for line in run(['nm', '-u', module]).splitlines() if line.split()]
    verify_symbols(symbols, exports, has_symvers)
    dependencies = run(['modinfo', '-F', 'depends', module])
    license_name = run(['modinfo', '-F', 'license', module])
    module_version = run(['modinfo', '-F', 'version', module])
    commit = os.environ['GITHUB_SHA']
    require(re.fullmatch(r'[0-9a-f]{40}', commit), 'Missing source commit')
    manifest = {
        'schema': 1, 'repository': os.environ['GITHUB_REPOSITORY'], 'source_commit': commit,
        'kernel_release': release, 'kernel_source_version': version, 'architecture': 'x86_64',
        'module_version': module_version, 'module_sha256': sha256(module), 'vermagic': vermagic,
        'license': license_name, 'dependencies': dependencies, 'compiler': run([gcc, '--version']),
        'kernel_source_url': source_url, 'kernel_source_sha256': source_hash,
        'target_bundle_sha256': expected_bundle_hash,
        'target_input_hashes': {p.name: sha256(p) for p in sorted(target.iterdir())},
        'prepared_config_sha256': sha256(kernel / '.config'),
        'symbol_validation': 'kernel-symvers-and-host-export-audit' if has_symvers else 'host-builtin-export-audit-without-symvers',
        'undefined_symbols': symbols,
        'run_url': f'https://github.com/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{os.environ["GITHUB_RUN_ID"]}',
        'host_load_tested': False, 'hardware_tested': False,
        'compiler_match': 'GCC major checked; full toolchain identity recorded, not asserted identical',
    }
    dist.mkdir()
    shutil.copyfile(module, dist / 'msi-ec.ko')
    shutil.copyfile(root / 'LICENSE', dist / 'LICENSE')
    (dist / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (dist / 'modinfo.txt').write_text(run(['modinfo', module]) + '\n')
    (dist / 'packages.txt').write_text(run(['dpkg-query', '-W']) + '\n')
    run(['git', 'archive', '--format=tar.gz', f'--output={dist / "msi-ec-source.tar.gz"}', commit])
    notes = (
        f'MSI-EC {module_version} for Linux {release}, x86_64\n\n'
        f'Source commit: {commit}\nSymbol validation: {manifest["symbol_validation"]}\n'
        'Built from the selected fork commit with exported target configuration and verified kernel source bytes.\n'
        'Checked ELF architecture, full reference vermagic and target-host exported symbol names.\n'
        'This is a prerelease: no module was loaded and no hardware controls were tested.\n'
        'Matching symbol names and vermagic do not prove ABI compatibility with vendor kernel patches.\n'
        'Without Module.symvers, symbol CRC/namespace metadata cannot be validated; only built-in symbol providers are accepted.\n'
        'Secure Boot/lockdown and target-host permissions still require local verification.\n'
        'SHA256SUMS verifies release files. Preserve build.json with the module.\n'
    )
    (dist / 'RELEASE-NOTES.txt').write_text(notes)
    (dist / 'SHA256SUMS').write_text(''.join(f'{sha256(p)}  {p.name}\n' for p in sorted(dist.iterdir())))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(notes)
    print('Build and artifact validation passed; no host/module/hardware actions performed.')


if __name__ == '__main__':
    main()
