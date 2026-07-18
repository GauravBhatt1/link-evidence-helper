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

You can also pass the name directly:

```bash
./find.py "Alpha"
```

## Search Results

```bash
./movie_report_finder.py "Alpha" --show-results
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
