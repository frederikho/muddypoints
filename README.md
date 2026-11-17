# Course Boards

A small, self-hosted Padlet alternative for lectures. Each class gets one **board**: you upload the lecture slides (PDF or images), share the link, students look at the material before class and **anonymously** comment on and like the slides they don't understand. After class you export everything as a **PDF** (slides plus comments) for OLAT and load next week's material onto the same link.

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

1. Open the board, **Export PDF with comments**, and upload the PDF to OLAT.
2. **Upload** the new material with *Replace all slides*. A PDF of the old state is archived automatically (see **Archive**).
3. The link stays the same, so students keep using their bookmark.

Or ask Claude: *"Export the comments of board 'Lecture 3' and replace it with https://…/lecture4.pdf"*.

Other useful buttons: **Close comments** (locks the board) and the ↑ ↓ 🗑 controls on each slide.

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
| Variable *(optional)* | `MAIL_FROM` | A sender address verified in Amazon SES (needed for password-reset and invite emails, see step 4) |
| Variable *(optional)* | `ALLOWED_EMAIL_DOMAINS` | e.g. `uni-example.de`. Only these emails may register. **Recommended.** |
| Variable *(optional)* | `STACK_NAME` | Defaults to `padlet`. Must match `AppStackName` from step 1. |
| Variable *(optional)* | `FRONTEND_URL` | Only if you use a custom domain for Pages |

**Settings → Pages → Source:** select **GitHub Actions**.

### 3. Deploy

Push to `main`, or open **Actions → Test & Deploy → Run workflow**. The workflow runs the tests, deploys the AWS stack, publishes the site at `https://<owner>.github.io/<repo>/` with the API URL filled in, and tags the release (`deploy-YYYYMMDD-HHMMSS`).

Then open the site, register, create a space and a board.

### 4. Email (password reset, invitations)

Without `MAIL_FROM` the app still works, but emails are only written to the logs. To send real emails:

1. In the AWS console go to **Amazon SES → Identities → Create identity**, verify your sender address or domain (same region as the app).
2. A new SES account is in the *sandbox* and can only send to verified addresses. Request **production access** in SES (usually approved within a day), or verify each colleague's address.
3. Set the `MAIL_FROM` variable and redeploy.

### 5. Connect Claude (MCP)

Each admin does this once for themselves:

1. In the web app open **your name (top right) → Claude / MCP access → Create key**, then copy the connector URL (`https://….lambda-url.….on.aws/mcp/pk_…`). It is shown only once.
2. In Claude (web or desktop) open **Settings → Connectors → Add custom connector**, paste the URL, and leave OAuth empty.
3. Ask Claude something like *"Which boards do I have?"*.

Claude acts with **your** permissions, so editors can't delete via Claude either. Treat the URL like a password; you can revoke it on the same page.

For code changes, also connect the GitHub repository in Claude. Anything Claude pushes to `main` is tested and deployed automatically. `CLAUDE.md` tells Claude how the project works.

---

## Operations

**Logs**: everything (backend and browser errors) goes to CloudWatch log group `/aws/lambda/padlet-api` as JSON lines with a `request_id`, kept 30 days. Admins who own a space can ask Claude *"show me the errors of the last hour"* (MCP tool `get_logs`).

**Rolling back code**:
- *Normal*: revert the bad commit on `main` (`git revert <sha>`, or ask Claude to). The pipeline redeploys.
- *Emergency*: **Actions → Test & Deploy → Run workflow** with `ref` set to an earlier `deploy-…` tag. That redeploys the old version right away while `main` is fixed.

**Rolling back data**: DynamoDB has point-in-time recovery (35 days) and the S3 bucket keeps old versions for 30 days. Restore through the AWS console. Before a board is cleared or replaced, a PDF of it is archived automatically.

**Cost**: for a few courses this is effectively free or a few cents per month (Lambda, DynamoDB on-demand, S3; the 1 GB of slide images costs about $0.03/month).

**Security notes**: passwords are PBKDF2-hashed; sessions are signed tokens that are revoked on password change; API keys are stored hashed; S3 is private (slides are served through short-lived signed URLs); URL imports block internal addresses. Anonymous commenting is open by design, so lock old boards and use owner deletion for spam.
