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
| **Space** | Usually one course. Has members. |
| **Owner** | Can do everything, including deleting things and managing roles. |
| **Editor** | Invited member. Can do everything except delete (spaces, boards, slides, other people's comments) and change roles. |
| **Board** | One public link per class, e.g. `…/board.html?b=k3x9ab2c7d`. Anyone with the link can view it. |
| **Students** | Don't register. On first visit each student gets a random funny name (e.g. *Lazy Lizard*) that stays the same in that browser for 7 days. They can't change it. |

## Weekly workflow

1. Open the board, click **⚙** (top right) → **Export PDF with comments**, and upload the PDF to OLAT.
2. In the same panel, **Upload** the new material with *Replace all slides*. A PDF of the old state is archived automatically (listed under **Archive** in the panel).
3. The link stays the same, so students keep using their bookmark.

Or ask Claude: *"Export the comments of board 'Lecture 3' and replace it with https://…/lecture4.pdf"*.

Also in the ⚙ panel: **Close comments** (students can still read but no longer comment or like), background texture, rename. Single slides are moved/deleted with the ↑ ↓ 🗑 controls under each slide. On the space screen, each board card has its own ⚙ menu.

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
3. Save both in GitHub, then redeploy (push, or Actions → Run workflow):
   ```bash
   gh secret set BREVO_API_KEY          # paste the key when asked
   gh variable set MAIL_FROM -b "boards@your-university.de"
   ```

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

Good to know:
- Claude acts with **your** permissions. Editors can't delete things via Claude either.
- Treat the URL like a password. Revoke it any time on the same page (Claude / MCP access → **Revoke**) and create a new one.
- Nothing happens? Ask an owner to check the logs, or ask Claude *"show the Muddy Points errors of the last hour"* once the connector works for someone.

### 6. Change the app with Claude (claude.ai/code)

Admins can change the app itself (layout, features, texts) by describing the change to Claude. Claude edits the code, runs the tests and opens a **pull request**. Once you merge it on GitHub, it is live a few minutes later.

**One-time setup (per admin)**
1. You need a GitHub account with **write access** to this repository (the repo owner adds you under *Settings → Collaborators*) and a Claude plan that includes Claude Code.
2. **Connect GitHub to Claude:** in Claude click your profile icon (bottom left) → **Settings → Connectors**, find **GitHub**, click **Connect**, and confirm with **Authorize** on the GitHub page.
3. Open **claude.ai/code**. If asked, **install the Claude GitHub App** and give it access to this repository (it lets Claude push branches and open pull requests).

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
