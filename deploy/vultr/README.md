# RefundOps on Vultr

This kit is for one dedicated Ubuntu 24.04 VM. Commands below are **operator
instructions**, not commands already executed. Run local commands in PowerShell 7
from the repository root; run the scripts as root on the VM. Replace every angle
bracket placeholder before running commands. Prerequisites: `vultr-cli` v3.11,
OpenSSH (`ssh`, `scp`, `ssh-keygen`), Git, and the project's Python environment.

`VULTR_API_KEY` is used **only in the operator's shell** by `vultr-cli`. Never copy
it to the VM or app environment. The distinct `VULTR_INFERENCE_API_KEY` is an app
credential and is allowlisted. No environment file is included in the source
archive. Store the API key in the local shell securely before starting; do not
paste it into a script or commit it. There are no Vultr CLI calls in the VM scripts.

1. Create or select an SSH key, then register its public half.

   ```powershell
   ssh-keygen -t ed25519 -f "$HOME/.ssh/refundops_vultr"
   ssh-add "$HOME/.ssh/refundops_vultr"
   vultr-cli ssh-key create --name refundops-demo --key (Get-Content "$HOME/.ssh/refundops_vultr.pub" -Raw).Trim()
   $sshKeyId = '<SSH_KEY_ID>'
   ```

   Keep the private key local. The commands below assume this key is available
   through the SSH agent; start the local agent if needed. Alternatively add
   `-i "$HOME/.ssh/refundops_vultr"` to `ssh`/`scp` and configure an `IdentityFile`
   in your local SSH config for the VM so the Python uploader also finds it.

2. Create a dedicated firewall group. Open **TCP 22, 80, 443 only** for IPv4;
   IPv6 stays disabled on this VM. Limit SSH to your operator address when possible.

   ```powershell
   vultr-cli firewall group create --description refundops-demo
   $firewallId = '<FIREWALL_GROUP_ID>'
   vultr-cli firewall rule create $firewallId --ip-type v4 --protocol tcp --subnet '<OPERATOR_IPV4>' --size 32 --port 22
   vultr-cli firewall rule create $firewallId --ip-type v4 --protocol tcp --subnet 0.0.0.0 --size 0 --port 80
   vultr-cli firewall rule create $firewallId --ip-type v4 --protocol tcp --subnet 0.0.0.0 --size 0 --port 443
   ```

   HTTP is needed for certificate issuance/redirects. HTTPS uses TCP; HTTP/3 over
   UDP is deliberately not opened. Outbound access must permit apt, dependency and
   browser downloads, certificate issuance, and the application's external APIs.

3. Check plan availability, then provision Seattle, Ubuntu 24.04, 4 cores / 8 GB.
   Plan IDs and regional availability can change. List plans and inspect regional
   availability before substituting another plan if necessary.

   ```powershell
   vultr-cli plans list
   vultr-cli regions availability sea
   vultr-cli instance create --region sea --os 2284 --plan vhp-4c-8gb-amd --ssh-keys $sshKeyId --firewall-group $firewallId --label refundops-demo --host refundops --ipv6=false
   $instanceId = '<INSTANCE_ID>'
   vultr-cli instance get $instanceId
   $vmIp = '<VM_IP>'
   $publicHost = '<PUBLIC_HOST>'
   ```

   Set `$publicHost` to `<ip-with-dashes>.sslip.io` using the returned public IPv4
   address. Do not include a scheme, path, or port. Wait until the instance is
   active and SSH responds. On the first SSH connection, verify the host key
   through the provider console or another trusted channel before accepting it.

4. Bootstrap the VM. This disables SSH password and keyboard-interactive login,
   so confirm public-key login first. It installs Python 3.12, uv, Caddy, Chromium
   system libraries, unattended security updates, and a locked service account.
   It can be rerun. Package installation and upgrades require network access on
   the VM; none are needed to inspect this kit offline.

   ```powershell
   ssh "root@$vmIp" 'true'
   scp deploy/vultr/bootstrap.sh "root@${vmIp}:/root/refundops-bootstrap.sh"
   ssh "root@$vmIp" 'bash /root/refundops-bootstrap.sh'
   ssh "root@$vmIp" 'test ! -f /var/run/reboot-required || echo REBOOT_REQUIRED'
   ```

   If requested, reboot with `ssh "root@$vmIp" 'reboot'` and wait for SSH again.
   Do not provision additional services on this dedicated VM: the kit owns the
   Caddy configuration and the `/opt/refundops` deployment directories.

5. Upload production environment settings from the operator machine. Prepare the
   local `.env` yourself; it is never uploaded wholesale. All these keys must be
   present and nonempty or the uploader refuses before contacting SSH:

   ```text
   PAYPAL_CLIENT_ID
   PAYPAL_CLIENT_SECRET
   PAYPAL_ENV
   PAYPAL_WEBHOOK_ID
   VULTR_INFERENCE_API_KEY
   OPENAI_API_KEY
   REFUNDOPS_CREDENTIAL_KEY
   REFUNDOPS_STRONG_MODEL
   REFUNDOPS_FAST_MODEL
   REFUNDOPS_EVIDENCE_MODEL
   DJANGO_SECRET_KEY
   ```

   ```powershell
   $python = 'C:/Users/jimmy/PycharmProjects/RefundOpsAI/.venv/Scripts/python.exe'
   & $python deploy/vultr/push_env.py "root@$vmIp" $publicHost
   ```

   Optional: `--env-file '<LOCAL_ENV_PATH>'`. The parser accepts single-line
   literal values, optional `export`, single/double quotes and trailing comments;
   variable expansion and multiline values are unsupported. Use literal values,
   not references to other variables. Values travel only on SSH stdin and are
   never printed, including on SSH failure. Only key names are reported. The
   remote file is atomically installed as `/etc/refundops/refundops.env`, mode
   `0640`, owner `root`, group `refundops`.

   The uploader always sets `DJANGO_DEBUG=false`,
   `DJANGO_ALLOWED_HOSTS=<PUBLIC_HOST>,localhost,.localhost`, and
   `REFUNDOPS_SITE_HOSTS=portal.localhost,tracking.localhost,inbox.localhost,store.localhost`.
   Keep `REFUNDOPS_CREDENTIAL_KEY` unchanged across deployments so existing vault
   records remain decryptable. Use a strong Django secret and appropriate PayPal
   environment/webhook credentials. Changes take effect on the next app restart.

6. Archive the reviewed source revision and deploy. `git archive` includes only
   committed files. The lead must commit the reviewed kit and app changes before
   these commands; otherwise archive an already-reviewed commit containing them.
   Do not include a tracked environment file or runtime data in that revision.
   `--output` avoids PowerShell redirecting binary archive bytes through text.

   ```powershell
   git archive --format=tar --output=deploy/vultr/refundops-src.tar HEAD
   scp deploy/vultr/refundops-src.tar "root@${vmIp}:/root/refundops-src.tar"
   scp deploy/vultr/deploy-app.sh "root@${vmIp}:/root/refundops-deploy.sh"
   ssh "root@$vmIp" "bash /root/refundops-deploy.sh $publicHost"
   Remove-Item -LiteralPath deploy/vultr/refundops-src.tar
   ```

   Deploy stops the app, replaces the source tree, runs `uv sync --frozen --no-dev`
   with Python 3.12, installs the locked Playwright Chromium browser as `refundops`,
   refreshes browser system dependencies as root, migrates, collects static files,
   seeds mock sites without resetting their shared date, and restarts the services.
   It waits for `/health/` on the local upstream. HTTPS certificate provisioning
   may take longer; verify it separately below. No Django superuser is created.

   The service has one ASGI worker on loopback port 8000. It loads
   `deploy.vultr.production_settings` to put SQLite/WAL and media under
   `/var/lib/refundops` and trust Caddy's forwarded HTTPS scheme. Base settings
   already enable SQLite WAL. Collected static assets remain in the source tree
   and are served by WhiteNoise. Source and virtualenv become root-owned/read-only
   to the service after setup. Browser downloads, cache and temporary files live
   under `/var/lib/refundops`; the service can write only there. Chromium runs
   directly as the unprivileged service account.

   Caddy obtains HTTPS automatically for the public hostname, limits request
   bodies to 25 MB, and flushes streamed/SSE responses immediately. Mock sites use
   HTTP port 80 and explicit loopback name resolution installed by bootstrap;
   Caddy rejects non-loopback clients requesting those hostnames. Mock forms need
   ordinary CSRF cookies over loopback HTTP, so the override retains Django's
   default CSRF cookie behavior; public session cookies are HTTPS-only.

7. Smoke test from the operator shell, without disabling certificate verification.

   ```powershell
   curl.exe --fail --show-error "https://$publicHost/health/"
   curl.exe --fail --show-error "https://$publicHost/"
   ssh "root@$vmIp" 'systemctl is-active refundops caddy'
   ssh "root@$vmIp" 'curl --fail --show-error http://portal.localhost/'
   ```

   In a browser, exercise a complete demo investigation, verify mock-site visits,
   evidence, static files, and SSE updates. Health alone does not validate those.
   If needed inspect `systemctl status refundops caddy` and `journalctl -u
   refundops -u caddy` **locally on the VM**; application logs can contain sensitive
   business data, so do not publish them.

8. Redeploy by repeating step 6 with the new reviewed revision. Repeat step 5 only
   when credentials/models/hostname change. To apply only an environment change:
   `ssh "root@$vmIp" 'systemctl restart refundops'`. Bootstrap is not needed for
   ordinary redeploys. The data directory survives source replacement. There is
   downtime during setup. One previous source tree is kept at
   `/opt/refundops.previous`; failure after stopping the app requires operator
   repair/redeployment. No automatic database rollback is attempted. Back up
   `/var/lib/refundops` and the environment securely before schema changes; stop
   the service or use SQLite's online backup API rather than copying a live DB
   without its WAL. Browser versions may accumulate in the data directory.

9. Destroy the demo when finished. Save required evidence/data securely first;
   deleting the VM destroys its local disk and cannot be undone. These commands
   use the saved IDs, not labels. Delete the dedicated firewall/key only if unused
   elsewhere; removing the cloud SSH key does not delete your local private key.

   ```powershell
   vultr-cli instance delete $instanceId
   vultr-cli instance list
   vultr-cli firewall group delete $firewallId
   vultr-cli ssh-key delete $sshKeyId
   Remove-Item Env:VULTR_API_KEY
   ```

Offline validation (no VM, SSH, package installation or API calls):

```powershell
bash -n deploy/vultr/bootstrap.sh
bash -n deploy/vultr/deploy-app.sh
& $python -m py_compile deploy/vultr/push_env.py deploy/vultr/production_settings.py
& $python -m ruff check deploy/vultr
& $python -m pytest deploy/vultr/tests -q -p no:django
```

`-p no:django` keeps these pure uploader tests from loading application settings
or a local environment file. ShellCheck can additionally check both shell scripts
if already installed. VM package availability, systemd/Caddy configuration,
Chromium launch, TLS/DNS, PayPal/inference integration and complete demo flows
require a real deployment and have not been exercised by this kit's offline checks.
