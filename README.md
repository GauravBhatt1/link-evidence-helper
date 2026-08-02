# Report Link Tools

Small Termux-friendly Python tools for collecting report-ready URL evidence from
movie listing pages without downloading movie files.

## Install on Termux

```bash
pkg update -y
pkg install git python curl -y
git clone https://github.com/GauravBhatt1/link-evidence-helper.git
cd link-evidence-helper
chmod +x *.py
```

## Easiest Mode

```bash
./find.py
```

Then enter the movie name, choose the result number, and press Enter for 1080p.
The first final link is printed immediately. On Termux, install clipboard
support to auto-copy it:

```bash
pkg install termux-api
```

You can also pass the name directly:

```bash
./find.py "Alpha"
```

Quality can be selected by number:

```text
Quality:
1. 480p
2. 720p
3. 1080p
4. 2160p / 4K
5. All qualities
```

When multiple final links are found, the output is numbered too. Type the link
number to copy only that link, or press Enter for `all`.

You can also use numbers from the command line:

```bash
./find.py -q 2 "Alpha"     # 720p
./find.py -q 5 "Alpha"     # all qualities
```

For less load and faster results, choose one quality. In easy mode the script
stops scanning that quality as soon as it finds a usable final link. `All
qualities` is heavier because it checks each quality separately.

## Search Results

```bash
./movie_report_finder.py "Alpha" --show-results
```

## Web UI

Run locally or on a VPS:

```bash
./web_app.py --host 0.0.0.0 --port 8765 --token "change-me"
```

Then open:

```text
http://SERVER_IP:8765/?token=change-me
```

## Extract 1080p Report Links

```bash
./movie_report_finder.py "Alpha" --pick 1 --quality 1080p
```

## CSV Output

```bash
./movie_report_finder.py "Alpha" --pick 1 --quality 1080p --output csv > report.csv
```

## Batch Mode

Create a text file with one movie name per line:

```bash
nano movies.txt
```

Run:

```bash
./movie_report_finder.py --batch movies.txt --quality 1080p --output csv > report.csv
```

## Safety

These scripts are evidence tools. They fetch HTML pages and inspect redirect
headers/landing pages. They do not download or mirror movie files.

## Restructuring scaffold

Milestone 1 adds contract and development-tooling foundations only. The Python
application remains the production implementation on port 8765; no React, Go,
Redis, PostgreSQL, or browser-worker feature receives production traffic.

- Canonical public schemas: `packages/contracts/schema/`
- Sanitized behavioral fixtures: `packages/testing/fixtures/`
- Python parity harness: `packages/testing/python/`
- Development-only dependencies: `infra/docker/compose.milestone1.yml`
- Migration documentation: `docs/migration/`

The development Compose file is opt-in, uses isolated volumes and an internal
network, publishes no host ports, and never loads production data:

```bash
docker compose -f infra/docker/compose.milestone1.yml config
```

Contract checks:

```bash
python3 packages/testing/python/export_contract_fixtures.py --check
pnpm typecheck
pnpm test:contracts
```
