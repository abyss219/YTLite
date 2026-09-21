#!/usr/bin/env python3
"""Build abyss219's YouTube IPA and check the injected code and resources."""
import argparse
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import plistlib
import posixpath
import re
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
from urllib.parse import urlsplit
import zipfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def deb_files(path):
    """Read regular payload files without extracting the Debian archive."""
    data = Path(path).read_bytes()
    require(data.startswith(b'!<arch>\n'), 'Invalid DEB archive')
    members, cursor = {}, 8
    while cursor < len(data):
        header = data[cursor:cursor + 60]
        require(len(header) == 60 and header[58:] == b'`\n', 'Invalid DEB member')
        name = header[:16].decode('ascii').strip().rstrip('/')
        size = int(header[48:58])
        cursor += 60
        require(size >= 0 and cursor + size <= len(data) and name not in members,
                'Invalid DEB member extent or duplicate')
        members[name] = data[cursor:cursor + size]
        cursor += size + size % 2
    require(cursor == len(data) and members.get('debian-binary') == b'2.0\n',
            'Invalid Debian package format')
    payloads = [v for k, v in members.items() if k.startswith('data.tar')]
    require(len(payloads) == 1, 'Expected one DEB payload')
    result = {}
    with tarfile.open(fileobj=io.BytesIO(payloads[0])) as archive:
        for member in archive:
            if member.isfile():
                require(member.name not in result, 'Duplicate DEB payload member')
                result[member.name] = archive.extractfile(member).read()
    return result


def verify_deb(path, expected):
    require(re.fullmatch(r'[0-9a-f]{64}', expected) is not None, 'Invalid expected SHA-256')
    actual = digest(path)
    require(actual == expected, 'Bundled DEB checksum mismatch')
    files = deb_files(path)
    libraries = [v for k, v in files.items() if PurePosixPath(k).name == 'YTLite.dylib']
    require(len(libraries) == 1, 'Expected exactly one YTLite.dylib in the DEB')
    require(executable_sections(libraries[0]), 'DEB library has no ARM64 executable sections')
    print('Verified bundled DEB SHA-256:', actual, flush=True)
    return files


def macho_image(binary):
    """Return the ARM64 slice and its bounded Mach-O load commands."""
    if binary[:4] in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        fat64 = binary[:4] == b'\xca\xfe\xba\xbf'
        require(len(binary) >= 8, 'Truncated universal binary')
        count = struct.unpack_from('>I', binary, 4)[0]
        width = 32 if fat64 else 20
        require(8 + count * width <= len(binary), 'Truncated universal slice table')
        candidates = []
        for index in range(count):
            offset = 8 + index * width
            cpu, subtype = struct.unpack_from('>II', binary, offset)
            start, size = struct.unpack_from('>QQ' if fat64 else '>II', binary, offset + 8)
            require(start + size <= len(binary), 'Invalid universal slice extent')
            if cpu == 0x100000C:
                candidates.append((subtype & 0xFFFFFF, binary[start:start + size]))
        require(candidates, 'IPA does not contain ARM64 machine code')
        binary = min(candidates, key=lambda item: item[0])[1]
    require(len(binary) >= 32 and binary[:4] == b'\xcf\xfa\xed\xfe',
            'Expected a little-endian 64-bit Mach-O binary')
    require(struct.unpack_from('<I', binary, 4)[0] == 0x100000C, 'Expected ARM64 machine code')
    count, length = struct.unpack_from('<II', binary, 16)
    end, cursor, commands = 32 + length, 32, []
    require(end <= len(binary), 'Truncated Mach-O load commands')
    for _ in range(count):
        require(cursor + 8 <= end, 'Truncated Mach-O command')
        command, size = struct.unpack_from('<II', binary, cursor)
        require(size >= 8 and cursor + size <= end, 'Invalid Mach-O command size')
        commands.append((command, binary[cursor:cursor + size]))
        cursor += size
    require(cursor == end, 'Incorrect Mach-O command extent')
    return binary, commands


def load_paths(binary, kinds):
    paths = []
    for kind, command in macho_image(binary)[1]:
        if kind in kinds:
            require(len(command) >= 12, 'Truncated Mach-O path command')
            start = struct.unpack_from('<I', command, 8)[0]
            require(12 <= start < len(command), 'Invalid Mach-O path offset')
            paths.append(command[start:].split(b'\0', 1)[0].decode())
    return paths


DYLIB_COMMANDS = {0xC, 0x80000018, 0x8000001F, 0x80000023}


def executable_sections(binary):
    image, commands = macho_image(binary)
    result = {}
    for kind, command in commands:
        if kind != 0x19:
            continue
        require(len(command) >= 72, 'Truncated segment command')
        count = struct.unpack_from('<I', command, 64)[0]
        require(72 + count * 80 <= len(command), 'Truncated section table')
        for index in range(count):
            offset = 72 + index * 80
            flags = struct.unpack_from('<I', command, offset + 64)[0]
            if not flags & (0x80000000 | 0x400):
                continue
            address, size, file_offset = struct.unpack_from('<QQI', command, offset + 32)
            require(file_offset + size <= len(image), 'Truncated executable section')
            key = command[offset:offset + 32]
            require(key not in result, 'Duplicate executable section')
            result[key] = (address, image[file_offset:file_offset + size])
    return result


def inspect_ipa(path, *, clean=False):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        names = {item.filename for item in members}
        require(len(names) == len(members), 'Duplicate IPA archive entries')
        for item in members:
            parts = PurePosixPath(item.filename).parts
            require(not item.filename.startswith('/') and '..' not in parts
                    and '\\' not in item.filename, 'Unsafe path in IPA archive')
            require(not stat.S_ISLNK(item.external_attr >> 16),
                    'IPA symlink entries are not supported by this build wrapper')
        require(sum(item.file_size for item in members) <= 8 * 1024**3, 'IPA expands beyond 8 GiB')
        infos = [n for n in names if re.fullmatch(r'Payload/[^/]+\.app/Info\.plist', n)]
        require(len(infos) == 1, 'IPA must contain exactly one main application')
        root = infos[0].rsplit('/', 1)[0]
        info = plistlib.loads(archive.read(infos[0]))
        executable = info.get('CFBundleExecutable', '')
        require(executable and '/' not in executable and executable not in ('.', '..'),
                'Invalid main executable name')
        binary = archive.read(root + '/' + executable)
        for kind, command in macho_image(binary)[1]:
            if kind in (0x21, 0x2C):
                require(len(command) >= 20, 'Truncated encryption command')
                require(struct.unpack_from('<I', command, 16)[0] == 0,
                        'YouTube IPA is encrypted; supply a decrypted IPA')
        if clean:
            require(info.get('CFBundleIdentifier') == 'com.google.ios.youtube',
                    'Supply a clean YouTube IPA with its original bundle identifier')
            require(not any(PurePosixPath(n).name == 'YTLite.dylib' for n in names),
                    'Input already contains YTLite; supply a clean YouTube IPA')
            require(not any('YTLite' in n for n in load_paths(binary, DYLIB_COMMANDS)),
                    'Input already loads YTLite')
        return root, info, binary


def expand_path(path, location, root):
    for prefix, base in (('@loader_path/', posixpath.dirname(location)),
                         ('@executable_path/', root)):
        if path.startswith(prefix):
            return posixpath.normpath(base + '/' + path[len(prefix):])
    return path


def resolve_dependency(dependency, location, root, runpaths, names):
    if dependency.startswith(('/System/Library/', '/usr/lib/')):
        return None  # Supplied by iOS and its shared cache.
    if dependency.startswith('@rpath/'):
        candidates = [posixpath.normpath(base + '/' + dependency[7:]) for base in runpaths]
    else:
        candidates = [expand_path(dependency, location, root)]
    for target in candidates:
        if target.startswith(root + '/') and target in names:
            return target
    # Cyan's hook runtime imports Swift from its /usr/lib/swift LC_RPATH.
    if any(re.fullmatch(r'/usr/lib/swift/libswift[A-Za-z0-9_]+\.dylib', p) for p in candidates):
        return None
    raise ValueError('Missing or unresolved injected dependency: ' + dependency)


def verify_output(path, package_files, name, bundle_id):
    root, info, main_binary = inspect_ipa(path)
    require(info.get('CFBundleIdentifier') == bundle_id, 'Output bundle identifier mismatch')
    require(info.get('CFBundleDisplayName') == name, 'Output display name mismatch')
    loads = load_paths(main_binary, DYLIB_COMMANDS)
    require(loads.count('@rpath/YTLite.dylib') == 1, 'YTLite must be injected exactly once')
    require('@executable_path/Frameworks' in load_paths(main_binary, {0x8000001C}),
            'Missing Frameworks runtime search path')
    with zipfile.ZipFile(path) as archive:
        library = archive.read(root + '/Frameworks/YTLite.dylib')
        original = next(v for k, v in package_files.items() if PurePosixPath(k).name == 'YTLite.dylib')
        require(executable_sections(library) == executable_sections(original),
                'Injection altered the audited executable sections')
        dependencies = load_paths(library, DYLIB_COMMANDS)
        require('@rpath/CydiaSubstrate.framework/CydiaSubstrate' in dependencies,
                'CydiaSubstrate dependency was not relocated')
        # Follow local dependencies from the injected library, including its hook runtime.
        main_location = root + '/' + info['CFBundleExecutable']
        runpaths = [expand_path(p, main_location, root)
                    for p in load_paths(main_binary, {0x8000001C})]
        pending = [(root + '/Frameworks/YTLite.dylib', library, runpaths)]
        names = set(archive.namelist())
        visited = set()
        while pending:
            location, binary, inherited = pending.pop()
            if location in visited:
                continue
            visited.add(location)
            runpaths = [expand_path(p, location, root) for p in load_paths(binary, {0x8000001C})] + inherited
            for dependency in load_paths(binary, DYLIB_COMMANDS):
                target = resolve_dependency(dependency, location, root, runpaths, names)
                if target is not None:
                    pending.append((target, archive.read(target), runpaths))
        resources = 0
        for filename, contents in package_files.items():
            parts = PurePosixPath(filename).parts
            bundle = next((i for i, part in enumerate(parts) if part.endswith('.bundle')), None)
            if bundle is not None:
                target = root + '/' + '/'.join(parts[bundle:])
                require(archive.read(target) == contents, 'Changed or missing resource: ' + target)
                resources += 1
        require(resources > 0, 'No package resources verified')
    print(f'Output verified: unchanged ARM64 code, {resources} resources, resolved hook dependencies.', flush=True)


def injection_files(deb, tweaks_dir):
    files, seen = [Path(deb).resolve()], {digest(deb)}
    if tweaks_dir:
        for extra in sorted(Path(tweaks_dir).glob('*.deb')):
            checksum = digest(extra)
            if checksum in seen:
                continue
            require(not any(PurePosixPath(n).name == 'YTLite.dylib' for n in deb_files(extra)),
                    'Additional package contains a conflicting YTLite library')
            files.append(extra.resolve())
            seen.add(checksum)
        files.extend(p.resolve() for p in sorted(Path(tweaks_dir).glob('*.appex')))
    return files


def build(args):
    require(re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', args.bundle_id) is not None,
            'Invalid bundle identifier')
    require(0 < len(args.name) <= 100 and not any(ord(c) < 32 for c in args.name), 'Invalid display name')
    require(not args.output.exists(), 'Output exists; choose a new output path')
    package_files = verify_deb(args.deb, args.sha256)
    tweaks = injection_files(args.deb, args.tweaks_dir)
    with tempfile.TemporaryDirectory(prefix='ytlite-abyss219-') as temporary:
        temporary = Path(temporary)
        source = args.input
        if args.ipa_url:
            url = urlsplit(args.ipa_url)
            require(url.scheme == 'https' and url.hostname and not url.username
                    and not any(c.isspace() for c in args.ipa_url), 'IPA URL must be a direct HTTPS URL')
            if os.environ.get('GITHUB_ACTIONS') == 'true':
                print('::add-mask::' + args.ipa_url.replace('%', '%25'), flush=True)
            source = temporary / 'YouTube.ipa'
            subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                            '--proto', '=https', '--proto-redir', '=https', '--retry', '2',
                            '--connect-timeout', '30', '--max-time', '900',
                            '--output', str(source), '--url', args.ipa_url], check=True)
        _, info, _ = inspect_ipa(source, clean=True)
        print('YouTube version:', info.get('CFBundleShortVersionString'), flush=True)
        print('Input IPA SHA-256:', digest(source), flush=True)
        output = temporary / 'YouTubePlus.ipa'
        subprocess.run([args.cyan, '-i', str(Path(source).resolve()), '-o', str(output),
                        '-u', '-w', '-e', '-f', *map(str, tweaks), '-n', args.name,
                        '-b', args.bundle_id], check=True)
        verify_output(output, package_files, args.name, args.bundle_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Publish only after validation; exclusive creation preserves any existing output.
        with output.open('rb') as src, args.output.open('xb') as dst:
            shutil.copyfileobj(src, dst)
    print('Output IPA SHA-256:', digest(args.output), flush=True)
    print('Created:', args.output, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for action in ('verify-deb', 'build'):
        command = commands.add_parser(action)
        command.add_argument('--deb', required=True, type=Path)
        command.add_argument('--sha256', required=True)
        if action == 'build':
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument('--input', type=Path)
            source.add_argument('--ipa-url')
            command.add_argument('--output', required=True, type=Path)
            command.add_argument('--tweaks-dir', type=Path)
            command.add_argument('--cyan', default='cyan')
            command.add_argument('--name', default='YouTube Plus')
            command.add_argument('--bundle-id', default='com.google.ios.youtube')
    args = parser.parse_args()
    try:
        if args.command == 'verify-deb':
            verify_deb(args.deb, args.sha256)
        else:
            build(args)
    except (ValueError, OSError, KeyError, struct.error, zipfile.BadZipFile,
            tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Build failed: {error}\n')


if __name__ == '__main__':
    main()
