<p align="center"><img src="frontend/img/logo-512.png" alt="Muddy Points logo" width="120"></p>

# Muddy Points

A small, self-hosted tool for collecting the *muddiest points* of a lecture. Each class gets one **board**: you upload the lecture slides (PDF or images), share the link, students look at the material before class and **anonymously** comment on and like the slides they don't understand. After class you export everything as a **PDF** (slides plus comments) for OLAT and load next week's material onto the same link.

Admins can do all of this from **Claude** too: through an MCP connector ("export this week's comments and replace the slides with this PDF") and, with the GitHub repo connected, by changing the app's code.

```
frontend/  static site (HTML/JS/CSS, no build step)        -> GitHub Pages
backend/   one Python Lambda: REST API (/api) + MCP (/mcp)  -> AWS Lambda Function URL
           DynamoDB (users, spaces, boards, comments, likes) + S3 (slide images, PDFs)
template.yaml                  AWS infrastructure (SAM)
.github/workflows/deploy.yml   test -> deploy backend -> deploy frontend -> tag release
```

## Concepts

| | |
|---|---|
| **Workspace** | Your teaching team (e.g. a chair). Has members and shared **files**. |
| **Board** | One course, with **one public link for the whole semester**, e.g. `…/board.html?b=k3x9ab2c7d`. Its content changes from session to session. Anyone with the link can view it. |
| **Session** | One piece of material on a board (usually one lecture PDF), with the time it goes live. |
| **Files** | Lecture PDFs and syllabi of a workspace. Kept permanently for reuse in later semesters. Each distinct PDF is stored only once. |
| **Owner** | Can do everything. |
| **Editor** | Can do everything except delete the workspace and manage owners. |
| **Students** | Don't register. On first visit each student gets a random funny name (e.g. *Lazy Lizard*) that stays the same in that browser for 7 days. They can't change it. |

## How a semester works

1. **Get the files in.** On the workspace page click **+ Upload files** (or ask Claude for an upload link). Drag in all lecture PDFs and the syllabus. Files that are already there are recognised and not uploaded twice.
2. **Plan the sessions.** On the board click **⚙ → Schedule** and add one session per lecture (file + time). Or ask Claude: *"Plan the ERE board from the syllabus"*. Claude reads the syllabus and proposes a schedule, and saves it once you confirm. By convention new material goes live right **after** the previous lecture ends.
3. **Lean back.** At each session time the board archives the current slides and comments as a PDF, shows the next lecture with fresh comments, and emails all owners and editors. Each person can switch these emails off per board (⚙ → Notifications). Before the first session students see *"Coming soon"*.
4. **For OLAT:** ⚙ → **Archive** has the PDF (slides plus comments) of every finished session. ⚙ → **Export PDF with comments** gives you the current one at any time.

Doing it by hand instead: ⚙ → **Upload** with *Replace all slides*, or **From workspace files**, or **History → Use again** to reuse earlier material.

Also in the ⚙ panel: **Close comments** (students can still read but no longer comment or like), background texture, rename. Single slides are moved/deleted with the ↑ ↓ 🗑 controls under each slide. On the workspace page each board card has its own ⚙ menu.

**Feedback:** the *💡 Feedback or feature idea* link at the bottom of every page sends a message to the maintainer.

---

## Getting started

### 0. Run it locally (no AWS needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements-dev.txt
python backend/dev_server.py          # http://localhost:8787  (in-memory fake AWS; data is lost on stop)
cd backend && pytest                  # test suite
```

Password-reset emails are printed to the console in local mode.

### 1. AWS: let GitHub deploy (one time, ~5 min)

You need an AWS account. Pick a region such as `eu-central-1` (Frankfurt).

1. In the AWS console open **CloudFormation → Create stack → Upload a template file** and choose `infra/github-oidc.yaml`.
   - `GitHubRepo`: `your-org/your-repo`
   - `CreateOIDCProvider`: `false` if your account already has a GitHub OIDC provider, otherwise `true`
2. When it finishes, copy the **RoleArn** from the stack's *Outputs* tab.

This gives GitHub Actions on `main` (and only there) a role that can deploy the app. No AWS keys are stored anywhere.

### 2. GitHub repository settings

**Settings → Secrets and variables → Actions:**

| Kind | Name | Value |
|---|---|---|
| Secret | `SECRET_KEY` | A long random string: `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`. Changing it later logs everyone out. |
| Variable | `AWS_ROLE_ARN` | The RoleArn from step 1 |
| Variable | `AWS_REGION` | e.g. `eu-central-1` |
| Secret *(optional)* | `BREVO_API_KEY` | Brevo API key for sending emails (see step 4) |
| Variable *(optional)* | `MAIL_FROM` | Sender address, verified in Brevo (or SES). Needed for password-reset and invite emails. |
| Variable *(optional)* | `ALLOWED_EMAIL_DOMAINS` | e.g. `uni-example.de`. Only these emails may register. **Recommended.** |
| Variable *(optional)* | `STACK_NAME` | Defaults to `muddypoints`. Must match `AppStackName` from step 1. |
| Variable *(optional)* | `FRONTEND_URL` | Only if you use a custom domain for Pages |

**Settings → Pages → Source:** select **GitHub Actions**.

### 3. Deploy

Push to `main`, or open **Actions → Test & Deploy → Run workflow**. The workflow runs the tests, deploys the AWS stack, publishes the site at `https://<owner>.github.io/<repo>/` with the API URL filled in, and tags the release (`deploy-YYYYMMDD-HHMMSS`).

Then open the site, register, create a space and a board.

### 4. Email (password reset, invitations)

Without `MAIL_FROM` the app still works, but emails are only written to the logs. Recommended: **Brevo** (free for 300 emails a day, can send to anyone):

1. Create an account at brevo.com. Under **Senders, Domains & Dedicated IPs** add and verify your sender address.
2. Under **SMTP & API → API Keys** create an API key.
   **Important:** under **Security → Authorised IPs** (https://app.brevo.com/security/authorised_ips) *deactivate* the blocking of unknown IP addresses. AWS Lambda has no fixed IP, and otherwise Brevo rejects every email with "unrecognised IP address".
3. Save both in GitHub, then redeploy (push, or Actions → Run workflow):
   ```bash
   gh secret set BREVO_API_KEY          # paste the key when asked
   gh variable set MAIL_FROM -b "boards@your-university.de"
   ```

Test it with **Forgot your password?** on the login page. Problems show up in the logs as `email sending failed` with Brevo's error message (ask Claude: *"show Muddy Points errors of the last hour"*). Only password resets and invitations send emails; registering does not.

Alternative: Amazon SES. Verify the sender in SES (same region), request production access (otherwise SES only sends to verified addresses), set only `MAIL_FROM`, and leave `BREVO_API_KEY` unset.

### 5. Connect Claude (MCP): manage boards by chatting

Each admin does this once, for their own account. Afterwards you can say things like *"Export this week's comments of Lecture 3"* or *"Replace the slides of Lecture 4 with this PDF: <link>"*.

**a) Create your personal key in Muddy Points**
1. Log in, click **your name** (top right) and open **Claude / MCP access**.
2. Enter a label (e.g. *Claude*) and click **Create key**.
3. Copy the **connector URL** that appears (`https://….lambda-url.eu-central-1.on.aws/mcp/pk_…`). It is shown **only once**. If you lose it, just create a new key.

**b) Add the connector in Claude** (claude.ai or the desktop app)
1. Open **Settings → Connectors** (bottom-left profile menu → Settings) and click **Add custom connector**.
2. **Name:** `Muddy Points`
3. **URL:** paste the connector URL from step a.
4. **Authentication:** choose **No authentication** (it is detected automatically; the key is already inside the URL). Leave *request headers* empty.
5. Click **Add**.

**c) Use it in a chat**
1. Start a new chat. In the **+** / tools menu under *Connectors*, make sure **Muddy Points** is switched on.
2. Ask: *"Which spaces and boards do I have in Muddy Points?"* The first time, Claude may ask permission to use the tools; allow it.

**d) Things to ask Claude**
- *"Give me an upload link for my lecture PDFs"*. You drag them in, then: *"Read them and put the right one on the ERE board"*.
- *"Plan the ERE board for the semester from the syllabus"*. Claude proposes the dates and times, you confirm, and it saves the schedule.
- *"What did we show in week 10 last year? Put it up again."* (board history)
- *"Export this week's questions for OLAT."*

**New slides via Claude**
- **PDF on your computer or attached in the chat:** say e.g. *"Replace the ERE slides with the new lecture"*. Claude replies with an **upload link**. Click it, log in if asked, and drop the PDF into the panel that opens. For a replacement, *Replace all slides* is already selected, and the old slides and comments are archived as PDF first.
- **PDF already online:** give Claude a Google Drive / Dropbox / public link (*"Replace ERE with this: <link>"*). Claude loads it directly, with no clicking needed.

(Claude can't send attached files to Muddy Points from a chat by itself, because its sandbox blocks outside servers. That's why it gives you the link.)

Good to know:
- Claude acts with **your** permissions. Editors can't delete things via Claude either.
- Treat the URL like a password. Revoke it any time on the same page (Claude / MCP access → **Revoke**) and create a new one.
- Nothing happens? Ask an owner to check the logs, or ask Claude *"show the Muddy Points errors of the last hour"* once the connector works for someone.

### 6. Change the app with Claude (claude.ai/code)

Admins can change the app itself (layout, features, texts) by describing the change to Claude. Claude edits the code, runs the tests and opens a **pull request**. Once you merge it on GitHub, it is live a few minutes later.

**One-time setup (per admin)**
1. You need a GitHub account with **write access** to this repository (the repo owner adds you under *Settings → Collaborators*) and a Claude plan that includes Claude Code.
2. **Connect GitHub to Claude:** in Claude click your profile icon (bottom left) → **Settings → Connectors**, find **GitHub**, click **Connect**, and confirm with **Authorize** on the GitHub page.
3. Open **claude.ai/code**. If asked, **install the Claude GitHub App** and give it access to this repository (it lets Claude push branches and open pull requests). Direct link: <https://github.com/apps/claude/installations/select_target>. In an organization, an **org owner** may have to do this. The app needs **write** access to the repository's contents and pull requests.

If Claude reports *"Claude doesn't have GitHub access"* or a push fails with **403**, this step is missing or incomplete. Install the app (link above) or reconnect GitHub under **Settings → Connectors → GitHub**, then tell Claude to try again. Until the push works, the changes exist only inside the temporary session and are lost when it ends.

**Making a change**
1. On claude.ai/code start a session on this repository and describe what you want, e.g. *"Show the date next to each comment"*.
2. Claude works in a cloud sandbox (test dependencies install automatically), runs the tests and opens a pull request with a plain-language summary.
3. On GitHub, open the pull request, wait for the green ✓ (tests), check the summary, then click **Merge pull request**.
4. A few minutes later it's live (watch it under the repo's **Actions** tab). Check it in the browser as described in the pull request.

If something goes wrong, ask Claude on claude.ai/code to *"revert the last change"*. It opens a pull request that undoes it; merge that. See *Rolling back code* below.

---

## Operations

**Logs**: everything (backend and browser errors) goes to CloudWatch log group `/aws/lambda/muddypoints-api` as JSON lines with a `request_id`, kept 30 days. Admins who own a space can ask Claude *"show me the errors of the last hour"* (MCP tool `get_logs`).

**Rolling back code**:
- *Normal*: revert the bad commit on `main` (`git revert <sha>`, or ask Claude to). The pipeline redeploys.
- *Emergency*: **Actions → Test & Deploy → Run workflow** with `ref` set to an earlier `deploy-…` tag. That redeploys the old version right away while `main` is fixed.

**Rolling back data**: DynamoDB has point-in-time recovery (35 days) and the S3 bucket keeps old versions for 30 days. Restore through the AWS console. Before a board is cleared or replaced, a PDF of it is archived automatically.

**Cost**: for a few courses this is effectively free or a few cents per month (Lambda, DynamoDB on-demand, S3; the 1 GB of slide images costs about $0.03/month).

**Security notes**: passwords are PBKDF2-hashed; sessions are signed tokens that are revoked on password change; API keys are stored hashed; S3 is private (slides are served through short-lived signed URLs); URL imports block internal addresses. Anonymous commenting is open by design, so lock old boards and use owner deletion for spam.
