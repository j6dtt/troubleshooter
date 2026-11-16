# Deploy: several hosts and the web page

For an install that already runs `./ts` on each host from one shared
folder. Steps 1 to 3 are done once, because every host reads the same
folder.

## Step 1: copy the files into `src/troubleshooter/`

Changed or new in this release:

```
cases.py
cli.py
config.py
engine.py
help.txt
history.py
hosts.py      (new)
launcher.py
tools.py
watch.py
web.py        (new)
webpage.py    (new)
```

## Step 2: edit `compose.yaml` in two places

**Place 1:** in the settings block, after the `record-minutes` line, add:

```yaml
  kubernetes: &kubernetes "on"
  hub-host: &hub-host "HUBNAME"
  agent-hosts: &agent-hosts "HOST2,HOST3"
  agent-port: &agent-port "8001"
  web-port: &web-port "9001"
```

- **HUBNAME:** what `hostname` prints on the hub.
- **HOST2,HOST3:** what `hostname` prints on each other host,
  separated by commas.

**Place 2:** under `environment:`, after the `RECORD_MINUTES` line, add:

```yaml
      KUBERNETES: *kubernetes
      HUB_HOST: *hub-host
      AGENT_HOSTS: *agent-hosts
      AGENT_PORT: *agent-port
      WEB_PORT: *web-port
```

## Step 3: make the keys and your account

Run these on the hub, in the troubleshooter folder.

**3a. Keys for the hosts to talk to each other.** Always needed, even
with your own certificate:

```
./ts setup
```

**3b. Your own certificate for the web page** (optional; without it
the browser warns):

```
./ts setup --web-cert /path/to/your-cert.pem /path/to/your-key.pem
```

Both files in PEM form, the key without a passphrase, and the hub's
name in the certificate as people type it in the browser.

**3c. Your account for the web page.** It asks for a password twice:

```
./ts user add YOURNAME
```

## Step 4: start it on every host

On each host, in the troubleshooter folder:

```
docker compose up -d
```

## Step 5: check, on the hub

```
./ts check
```

Look for these lines:

```
[ok] Agent HOST2: answered in ...
[ok] Agent HOST3: answered in ...
[ok] Web page: https://HUBNAME:9001, 1 account(s)
[ok] Web page certificate: your own, runs out in ... days
```

A line that says FAIL states the reason.

## Step 6: open the page

```
https://HUBNAME:9001
```

Sign in with YOURNAME and the password from 3c. The page shows Docker
and the hosts. Kubernetes comes with step 7.

## Step 7: add Kubernetes to the page

The page runs all the time, so it can't use your personal kubectl
login, which only exists while `./ts` is open. It needs logins saved
as a file in the `kube` folder.

**7a.** On the hub, log in with kubectl using the service accounts.

**7b.** Save those logins into one file:

```
kubectl config view --raw --flatten > kube/vks.yaml
chmod 644 kube/vks.yaml
```

**7c.** Check that the container can use the file:

```
grep -c "exec:" kube/vks.yaml
```

- **0:** good.
- **1 or more:** the login runs a program that is not in the
  container. Use a kubeconfig that holds a token instead.

**7d.** Restart the hub and check:

```
docker compose up -d
./ts check
```

Each cluster appears as `[ok] Context vks.<name>`, and the Health
section of the page then shows them.

Two things to know:

- **The file holds login tokens,** and `chmod 644` makes it readable
  by anyone who can read the shared folder. The container needs that
  on a shared folder.
- **If the tokens expire,** the page shows the clusters as "login
  refused". Repeat 7a and 7b.

## Later

- **Another account:** `./ts user add NAME`, then
  `docker compose up -d` on the hub.
- **Another host:** add it to `agent-hosts`, then
  `docker compose up -d` on the hub and on the new host.
- **Docker only, no Kubernetes:** set `kubernetes` to `"off"`, then
  `docker compose up -d`.
