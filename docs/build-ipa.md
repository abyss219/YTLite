# Build YouTube Plus — abyss219

This branch packages abyss219's audited YouTube Plus 5.2.2 DEB into a decrypted YouTube IPA. The package is stored directly in `debs/`; all DEBs share that directory. Its external filename identifies this edition. Renaming preserves the audited package contents and their checksum.

The build reads the DEB from the selected repository commit. The expected SHA-256 lives in `.github/workflows/build-abyss219.yml`. There are no separate JSON manifests or verification-report files to maintain.

## Run on GitHub

1. Open [Actions → Build YouTube Plus — abyss219](https://github.com/abyss219/YTLite/actions/workflows/build-abyss219.yml).
2. Click **Run workflow** and leave **Use workflow from: main** selected. The small launcher on `main` calls the build implementation on `abyss219-build`; the patched DEB stays on that branch. Selecting `abyss219-build` also runs the implementation directly.
3. Enter a direct HTTPS URL for a clean, decrypted YouTube IPA. The GitHub runner must be able to download it without an interactive login. The URL must stay valid for the duration of the job. A local Mac or iCloud filesystem path cannot be used here.
4. Set the app name and bundle identifier, choose any optional integration checkboxes, and optionally enable a draft release. The bundled DEB is selected automatically. Start with integrations disabled when checking a new YouTube version.
5. Click the green **Run workflow** button. Open the new run to follow its jobs.
6. Once it succeeds, download the **YouTubePlus-abyss219-<run number>** artifact. It contains `YouTubePlus_5.2.2_abyss219.ipa`. With **Also attach the IPA to a draft GitHub Release** enabled, the same IPA is attached to a draft release in your fork.
7. Sign and install the IPA using your sideloading tool, such as Sideloadly. This workflow does not provide an Apple provisioning profile or install the app on an iPhone.

The launcher is the only additional file on `main`. The DEB, Python build script, checks, and reusable build implementation remain on `abyss219-build`. The launcher grants the called workflow permission to create the optional draft release; its ordinary build jobs use read-only repository permissions.

Branch pushes only validate the bundled DEB and build checks. Use **Run workflow** for an IPA build. GitHub's browser form requires the dispatch workflow on the default branch, as described in [GitHub's manual-run documentation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

You can also run the same form through GitHub CLI:

```sh
gh workflow run build-abyss219.yml \
  --repo abyss219/YTLite \
  --ref main \
  -f ipa_url='https://your-host.example/YouTube.ipa'
```

Replace the example URL with your real download URL. To run the implementation directly, use `--ref abyss219-build`. Optional inputs:

| Input | Default |
| --- | --- |
| `display_name` | `YouTube Plus` |
| `bundle_id` | `com.google.ios.youtube` |
| `enable_youpip`, `enable_ytuhd`, `enable_yq` | `false` |
| `enable_ryd`, `enable_ytabc`, `enable_demc` | `false` |
| `create_draft_release` | `false` |

Add inputs with `-f display_name='YouTube Plus'`, `-f enable_ryd=true`, etc.

## What the workflow does

- Resolves the build branch once (or uses the triggering commit for a direct run), verifies the bundled DEB's exact checksum, and uses that same commit for the helper, IPA packaging, run summary, and optional release target.
- Calls the existing tweak helper with the repository DEB as its single package source. Existing optional integrations and the Safari extension use the helper's existing build path.
- Pins Cyan to commit `740d3716dcd98c20c000f12cdb88f1f0b2a533a4`.
- Rejects an encrypted main executable, a non-YouTube input, a previously injected YTLite library, duplicate ZIP entries, unsafe archive paths, and symlink entries.
- Removes the IPA's existing watch app and app extensions, then injects the bundled tweak and selected integrations, including the helper's Safari extension. This matches the existing repository's Cyan flags.
- Includes the primary DEB once, even when the helper stages another copy as `ytplus.deb`; rejects another package containing a conflicting YTLite library.
- Checks the resulting app name/identifier, exactly one YTLite load command, unchanged executable sections in the audited library, package resource bytes, and resolved local dependencies of YTLite and its hook runtime.
- Publishes the output only after those checks pass. Commit/tool/package information appears in the run summary; IPA hashes appear in the build log. Artifacts expire after seven days.

Cyan rewrites the library's dependency paths and signing data during injection. The package checksum therefore protects the input; the output check compares executable sections and resources rather than requiring the injected library's entire file hash to remain unchanged.

## Build locally on macOS

Python 3.11 or newer and macOS command-line tools are required. Install the pinned injector in a virtual environment:

```sh
python3 -m venv /private/tmp/ytlite-cyan
/private/tmp/ytlite-cyan/bin/python -m pip install \
  'https://github.com/asdfzxcvbn/pyzule-rw/archive/740d3716dcd98c20c000f12cdb88f1f0b2a533a4.zip'

python3 -B scripts/build_ipa.py build \
  --input '/absolute/path/YouTube.ipa' \
  --deb 'debs/com.dvntm.ytlite_5.2.2+abyss219_iphoneos-arm.deb' \
  --sha256 4eaa5dd92c586eff6cb8ce7a13817ab1a8ae59f876148a67e71400a388c6fdcd \
  --cyan /private/tmp/ytlite-cyan/bin/cyan \
  --output packages/YouTubePlus_5.2.2_abyss219.ipa
```

This local command injects the primary DEB. Pass `--tweaks-dir /path/to/prepared-tweaks` to include helper-built integrations and an `.appex` directory. The output path must be unused; the input IPA remains unchanged.

Run the focused checks with:

```sh
python3 -B -m unittest discover -s tests -p test_build_ipa.py -v
actionlint .github/workflows/build-abyss219.yml .github/workflows/_build_tweaks.yml
```

The DEB retains the previous static and CPU-emulation audit. IPA structure and successful injection do not prove real-device launch or every feature's compatibility with a particular YouTube release. Account-management UI and authentication network code remain present. Sign and test the resulting app on your iPhone before relying on it.

## Local validation performed

The complete base build was tested with decrypted YouTube **21.24.3**, the bundled DEB, pinned Cyan, and the Safari extension. Injection completed and the output checks passed: audited ARM64 executable sections preserved, 16 bundle resources preserved, and the hook runtime's dependencies resolved, including Swift libraries supplied by iOS. The original IPA was unchanged.

The 15 focused Python tests, six helper source-selection cases, and actionlint for the new workflow and updated helper passed. Optional integration combinations and real-device launch were not exercised by that local test.
