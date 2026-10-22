# LoL Replay Multimodal Search

A local prototype for finding moments in **League of Legends** matches from Riot timelines, then comparing short replay clips by gameplay context, event descriptions, and visual appearance.

The project supports two workflows:

- **Search match timelines:** natural-language queries, event filters, and a PCA view of the retrieved time windows.
- **Find similar clips:** rank short replays using three signals: event-derived text, structured gameplay features, and video frames.

> **Project status:** Multimodal clip retrieval is an unsupervised prototype built from handcrafted descriptors. It does not yet use a trained video encoder or a learned shared multimodal space. Local match data and replay clips are not included in this repository.

## Getting started

The `start.sh` launcher requires **Python 3.12** and creates `.venv` automatically. It runs on Linux or WSL. The League client on Windows is only needed to open or record new replays; searches over an existing index work offline.

From the repository root:

```bash
./start.sh setup
cp .env.example .env
```

Set `RIOT_API_KEY` in `.env` to import new matches. The key is not needed to search an existing index. Git ignores `.env`.

A fresh GitHub clone does not contain an index. Restore your own data under `data/`, or build an index with the pipeline below. Once the index is ready:

```bash
./start.sh doctor
./start.sh
```

The Streamlit app opens at <http://127.0.0.1:8501>. If an archived match is available, `./start.sh doctor --live` can also check the Riot API key.

## Build an index from Riot data

```bash
./start.sh cli ingest-summoner --riot-id 'GameName#EUW' --count 20
./start.sh cli build-windows --window-seconds 30 --step-seconds 10
./start.sh cli build-index
```

The pipeline saves matches and timelines in `data/raw/`, time windows in `data/processed/windows.jsonl`, and search vectors and metadata in `data/index/`. These data directories remain local and are ignored by Git.

By default, window descriptions are encoded with a 512-dimensional `HashingVectorizer`. The optional `--use-pca --pca-dim 128` flags reduce the index when it is built. This differs from the 2D PCA view in the app, which is recomputed for each set of search results to help explore them.

## Search for moments

```bash
./start.sh cli query --text 'teamfight around the dragon' --top-k 5
./start.sh cli query-precise --requires-dragon --min-kills 3 --top-k 5
./start.sh cli query-precise --no-kills --window-seconds 30 --top-k 10
```

Indexed windows include kills, objectives, gold swings, game phase, approximate map zones, jungle invades, and mid-lane roams. The app combines text search, filters, a results table, and a PCA projection. Timeline positions are sampled at a low frequency: movement features describe broad map patterns rather than second-by-second paths.

## Find similar replay clips

Place at least two complete clips aligned with the index in `data/clips/`, then run:

```bash
./start.sh cli similar-clips
./start.sh cli similar-clips \
  --clip CLIP_NAME.mp4.webm \
  --top-k 3 \
  --text-weight 1 --gameplay-weight 1 --video-weight 1
```

A clip filename contains its match ID and time interval. The program links it to the indexed window with the greatest overlap. It then computes one representation per signal:

| Signal | Current representation |
| --- | --- |
| Text | A short event-derived play summary, encoded with the index's text embedder (a 512-dimensional `HashingVectorizer` by default). |
| Gameplay | 14 features covering kills, objectives, gold swing, game phase, invades, and roams. |
| Video | Five sampled frames summarized by color histograms, edges, and temporal variation (257 dimensions). |

Cosine similarity is computed **separately** for each signal. The three scores are scaled to a relative 0–1 range across candidate clips, then combined using the chosen weights. They are ranking scores: **1 does not mean a perfect match**. The text is derived from game events, so it is not an independent data source. This workflow takes a *reference clip* as its query; a free-text query uses the timeline search described above.

In the app, the **Plays similaires** section shows each signal's score and lets you adjust its weight. Incomplete clips and clips without a matching indexed window are skipped.

## Open or export a replay

Video export requires the League client, an available replay, and the local Replay API enabled in `game.cfg`:

```ini
[General]
EnableReplayApi=1
```

Export a known interval:

```bash
./start.sh cli export-clip \
  --match-id EUW1_1234567890 \
  --start-ms 1250000 --end-ms 1280000 \
  --auto-fetch --auto-load
```

Search for a moment, open its replay, and record the selected result:

```bash
./start.sh cli query-open-result \
  --text 'teamfight around the dragon' \
  --top-k 8 --result-index 1 --auto-fetch
```

On WSL, the project can delegate replay opening and local API control to Windows. If needed, set the following in `.env`:

```dotenv
REPLAY_USE_LCU=1
REPLAY_LCU_EXECUTOR=windows
REPLAY_API_EXECUTOR=windows
```

`RIOT_CLIENT_PATH` and `LEAGUE_DIR` override automatic installation detection. Other integration options are listed in [.env.example](.env.example). The `web/` directory contains an older Next.js interface; Streamlit is the main workflow.

## Limitations and research direction

The nine clips in the local archive are enough to demonstrate that the signals can rank plays differently, but not enough to measure retrieval quality reliably. Current video descriptors mostly capture appearance and image variation; they do not explicitly represent the strategic intent behind a play. There are no human similarity judgments yet.

A research extension would collect annotated pairs of plays, encode video and event sequences separately, and learn to align their representations. Evaluation should compare video-only, event-only, and fused retrieval—for example with Recall@K and nDCG—on matches held out from training.
