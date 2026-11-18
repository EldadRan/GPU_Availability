# GPU availability tracker (Vast.ai + RunPod)

GitHub Actions snapshots GPU availability every 20 minutes, appends the readings to CSV files in `data/`, and GitHub Pages serves `index.html` as the dashboard.

## Setup (about 5 minutes)

1. Create a new GitHub repo and push these files to `main`. A public repo gives unlimited Actions minutes; a private one uses about 2,000 minutes a month, roughly the free allowance (change the cron to `*/30` to halve it).
2. Under **Settings → Secrets and variables → Actions → New repository secret**, add:
   - `RUNPOD_API_KEY`: a RunPod API key (read-only is enough).
   - `VAST_API_KEY`: optional. Vast offer search works without one.
3. Under **Settings → Actions → General → Workflow permissions**, choose **Read and write**.
4. Under **Settings → Pages**, set Source to **Deploy from a branch**, branch `main`, folder `/ (root)`.
5. On the **Actions** tab, open **Collect GPU availability**, click **Run workflow** once, and check that it passes.
6. Open `https://<user>.github.io/<repo>/`.

## After the first run

- Open `data/vast_gpu_names.json`. It lists the exact GPU names currently on Vast. If a name in `collector/config.json` → `vast_gpus` always shows 0, correct it to the name used there (for example the RTX PRO 6000 variants).
- `data/status.json` shows whether each provider succeeded on the last run. The dashboard shows it too.

## What is measured

**Vast.ai.** Rentable on-demand offers that aren't currently rented, per GPU name:
- available GPUs, in total and broken down as datacenter, Europe, fast network (≥2 Gbps down) and reliable hosts (≥98%)
- offer count
- cheapest, median and cheapest-Europe $/GPU-hr

Vast Serverless recruits from this same pool at the same prices.

**RunPod.** The stock level (High / Medium / Low / none) and price per GPU type, for Secure and Community cloud. RunPod doesn't publish serverless capacity separately, so the Pods stock level is a proxy.

## Regions

- **Vast:** each host's country is grouped into the regions defined in `collector/config.json` → `regions` (UK & Ireland, Europe West/North/South/East, North America, and so on). Edit them freely; any country not listed goes to "Other". On the dashboard, the **Vast region** filter limits the charts to one region, and the region table shows average availability per region.
- **RunPod:** stock per datacenter (EU-RO-1, US-TX-3, …). Pick one with the **RunPod datacenter** filter. A serverless endpoint that uses a network volume is tied to that volume's datacenter, so choose the datacenter where your GPUs have the most stock.
- Regional data goes into monthly files under `data/regions/`, so no single file grows too large. The dashboard loads up to the last 12 months.

## Notes

- GitHub can delay scheduled runs by a few minutes, and it pauses schedules on repos with no activity for 60 days. The data commits count as activity, so the schedule keeps running.
- Data grows by roughly 25 MB a year for the global files, plus about 10–20 MB a month for the regional files. Trim old rows or move them to an archive file if needed.
