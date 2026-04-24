Create a CalVer release for this project.

**Version format:** `vYEAR.N.PATCH`
- `YEAR` — current calendar year
- `N` — release number within the year, starting at 1
- `PATCH` — patch number, starting at 0

**Argument (optional, from `$ARGUMENTS`):**
- *(none)* — new release: bump N, reset PATCH to 0 (or start at `vYEAR.1.0` if no tags this year)
- `patch` — increment PATCH of the latest tag
- `2026.3.1` or `v2026.3.1` — use this exact version

---

Follow these steps exactly. Do not skip the confirmation.

1. Run `git tag --list 'v*' --sort=-version:refname | head -10` and note the latest tag.
2. Check today's date (use the date from the system context or run `date +%Y`).
3. Determine the new version from the argument and the rules above. Show the user:
   - The latest existing tag
   - The new version you intend to create
   - A one-line summary of what will change (new release vs patch)
   Then **ask for confirmation** before doing anything. Stop and wait for a yes/no reply.
4. Once confirmed, run these commands in order — stop immediately if any fails:
   a. Update the `version` field in `desktop/package.json` to the new version (no `v` prefix). Use the Edit tool, not shell.
   b. `git add desktop/package.json`
   c. `git commit -m "chore: release vVERSION"`
   d. `git tag vVERSION`
   e. `git push origin vVERSION`
5. Report the tag that was pushed and remind the user that the `docker-publish` and `desktop-build` CI workflows will now trigger.
