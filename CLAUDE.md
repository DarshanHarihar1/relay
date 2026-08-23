# Commit and authorship rules

These rules apply to every commit made in this repository.

## Author identity

Every commit must use only this author identity:

- Name: `Darshan Harihar`
- Email: `darshanharihar2950@gmail.com`

Before creating a commit, verify the repository-local Git configuration:

```bash
git config --local user.name
git config --local user.email
```

If either value is different, set it locally before committing:

```bash
git config --local user.name "Darshan Harihar"
git config --local user.email "darshanharihar2950@gmail.com"
```

Do not use a global Git identity when the repository-local identity is missing or incorrect.

## Attribution restrictions

- Do not add co-author trailers.
- Do not add tool-attribution trailers.
- Do not mention Codex, OpenAI, ChatGPT, Claude, Anthropic, or any other coding assistant in commit messages, trailers, release notes, or source comments created as part of a commit.
- Do not add `Co-authored-by` lines.
- The only commit author must be Darshan Harihar.
- Do not rewrite the author as an organization, bot, automation account, or service account.

## Commit message format

Use a short, clear message that describes the repository change. Prefer the conventional format:

```text
<type>: <imperative summary>
```

Examples:

```text
feat: add commitment extraction endpoint
fix: make action retries idempotent
docs: add gcloud setup guide
test: cover duplicate webhook delivery
chore: update locked dependencies
```

Keep the first line concise. Do not include credentials, access tokens, private URLs, or personal data in a commit message.

## Before every commit

1. Check the current branch and working tree:

   ```bash
   git status --short --branch
   ```

2. Review the exact staged files:

   ```bash
   git diff --cached --name-status
   git diff --cached --check
   ```

3. Confirm secrets are not staged. Check for `.env` files, service-account keys, private keys, OAuth codes, access tokens, and API keys. Never stage them.

4. Confirm the author identity:

   ```bash
   test "$(git config --local user.name)" = "Darshan Harihar"
   test "$(git config --local user.email)" = "darshanharihar2950@gmail.com"
   ```

5. Commit only the intended changes. Do not use broad staging commands when unrelated work is present.

6. Inspect the resulting commit:

   ```bash
   git show --summary --format=fuller HEAD
   git show --format='%an <%ae>%n%cn <%ce>%n%s%n%b' --no-patch HEAD
   ```

The author and committer must both be Darshan Harihar with the repository email. If a commit was created with the wrong identity, stop and fix it before pushing.

## Scope and branch rules

- Keep each commit focused on one logical change.
- Do not commit generated credentials, local gcloud configuration, emulator state, build artifacts, or unrelated user changes.
- Do not amend, rebase, reset, or force-push shared history unless Darshan explicitly requests it.
- Preserve existing user changes in a dirty worktree.
- Use a descriptive branch for work when a change is not ready for `main`.
- Keep `main` deployable and synchronized with the completed implementation.
- When a phase is complete, create its required commit before moving to the next phase.

## Push rules

- Push only after the commit has passed the appropriate tests and the staged diff has been reviewed.
- Push the completed phase to its working branch.
- If the project workflow requires `main` to contain the completed phase, update `main` explicitly and verify both remote branches point to the intended commit.
- Never force-push `main` or another shared branch without explicit approval.
- After pushing, verify the remote tracking state:

  ```bash
  git fetch origin
  git status --short --branch
  git branch -vv
  ```

## Verification and handoff

- Run the repository verification command documented for the current phase before claiming completion.
- Record known environment-only limitations, such as an unavailable Docker daemon or local emulator.
- Report the commit hash, branch, tests run, and any remaining limitation.
- If a push fails because of network or authentication, leave the local commit intact and report that the remote was not updated. Do not create a replacement commit just to retry the push.

## Secret handling

- Keep `.env`, ADC files, gcloud config directories, service-account JSON files, and private keys ignored by Git.
- Never print or paste secret values into terminal output, commit messages, documentation, or issue text.
- If a secret is accidentally exposed, stop using it and rotate it before continuing.
