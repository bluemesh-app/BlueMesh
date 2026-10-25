# Submitting BlueMesh to Flathub (do this yourself)

Flathub does not accept submission PRs opened by AI tools, so these steps are
for Dyl to do by hand, in the browser and a local terminal, as `bluemesh-app`.
Requirements: https://docs.flathub.org/docs/for-app-authors/requirements
Submission guide: https://docs.flathub.org/docs/for-app-authors/submission

1. Make sure the build works locally first (see `FLATPAK.md`): full
   `flatpak-builder` build from the git source, run the app, and get
   `flatpak-builder-lint` clean on the manifest and the repo.
2. Fork https://github.com/flathub/flathub on GitHub. Untick "Copy the
   master branch only" so the `new-pr` branch comes with the fork.
3. Clone your fork and branch off `new-pr`:
   ```sh
   git clone --branch=new-pr git@github.com:bluemesh-app/flathub.git
   cd flathub
   git checkout -b io.github.bluemesh_app.BlueMesh new-pr
   ```
4. Add the manifest at the top level of that repo, named
   `io.github.bluemesh_app.BlueMesh.yml`, plus `cargo-sources.json` next to
   it. Write it yourself or copy `packaging/io.github.bluemesh_app.BlueMesh.yml`
   and go through every line so you can explain it to reviewers. Keep the git
   source pinned to the release `tag:` and full `commit:`. Drop the comment
   block at the top.
5. Commit and push the branch to your fork.
6. Open the PR in the browser against `flathub/flathub`, base branch
   **`new-pr`** (not `master`). Title: `Add io.github.bluemesh_app.BlueMesh`.
   Fill in the PR template checklist yourself.
7. Say in the PR description that AI tools helped write the app code and the
   packaging, and that you have reviewed it and will maintain it.
8. Answer reviewer comments yourself. Once a reviewer comments
   `bot, build`, check the test build. After it is merged you get a
   `flathub/io.github.bluemesh_app.BlueMesh` repo for future updates.
