# Build YouTube Plus — abyss219

This branch packages abyss219's audited YouTube Plus 5.2.2 DEB into a decrypted YouTube IPA. The package is stored directly in `debs/`; all DEBs share that directory. Its external filename identifies this edition. Renaming preserves the audited package contents and their checksum.

The build reads the DEB from the selected repository commit. The expected SHA-256 lives in `.github/workflows/build-abyss219.yml`. There are no separate JSON manifests or verification-report files to maintain.

## Run on GitHub

1. Push the `abyss219-build` branch to your fork and enable Actions if GitHub prompts you.
2. Let **Build YouTube Plus — abyss219** finish its initial validation run. Branch pushes validate the bundled DEB and build checks; they do not download or build an IPA.
3. Supply a direct HTTPS URL for a clean, decrypted YouTube IPA. The GitHub runner must be able to download it without an interactive login. The URL must stay valid for the duration of the job.
4. Trigger an IPA build using GitHub CLI:

   ```sh
   gh workflow run build-abyss219.yml \
     --repo abyss219/YTLite \
     --ref abyss219-build \
     -f ipa_url='https://your-host.example/YouTube.ipa'
   ```

   Replace the example URL with your real download URL. Optional inputs:

   | Input | Default |
   | --- | --- |
   | `display_name` | `YouTube Plus` |
   | `bundle_id` | `com.google.ios.youtube` |
   | `enable_youpip`, `enable_ytuhd`, `enable_yq` | `false` |
   | `enable_ryd`, `enable_ytabc`, `enable_demc` | `false` |
   | `create_draft_release` | `false` |

   Add inputs with `-f display_name='YouTube Plus'`, `-f enable_ryd=true`, etc. Start with integrations disabled when checking a new YouTube version.

5. Open the completed run and download the **YouTubePlus-abyss219-<run number>** artifact. It contains `YouTubePlus_5.2.2_abyss219.ipa`. With `create_draft_release=true`, the same IPA is also attached to a draft release in your fork.
6. Sign and install the IPA using your sideloading tool, such as Sideloadly. This workflow does not provide an Apple provisioning profile or install the app on an iPhone.

GitHub's browser **Run workflow** button depends on the workflow being present on the default branch. A branch-only workflow can first run on `push` and then be dispatched through the CLI/API, as described in [GitHub's workflow event documentation](https://github.com/github/docs/blob/main/content/actions/reference/workflows-and-actions/events-that-trigger-workflows.md#workflow_dispatch). Once the workflow is present on the default branch, use the browser branch selector to choose `abyss219-build`.

## What the workflow does

- Checks out the triggering commit and verifies the bundled DEB's exact checksum.
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
