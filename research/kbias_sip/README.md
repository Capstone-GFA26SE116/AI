# K-SiP — replaying K_bias learning on the SiP dataset

Offline check of the FR-17 K_bias learning (`metanoia_ai.domain.kbias`) on real
estimate/actual pairs. No database, no LLM, no network. Plan: `plans/K-convergence.md`.

## Data

The SiP dataset is **not** stored in this repo (licence CC BY-NC-ND 4.0, see below).
Download it once:

```bash
git clone --depth 1 https://github.com/Derek-Jones/SiP_dataset ~/data/SiP_dataset
```

The script reads two files from that folder: `Sip-task-info.csv` and `est-act-dates.csv`
(both encoded in cp1252).

## Run

```bash
uv sync
uv run python -m research.kbias_sip.run --data ~/data/SiP_dataset --out research/kbias_sip/results
```

Output goes to `results/`: CSV files (ignored by Git), three PNG charts and `results.md`.
Running twice gives the same numbers (no randomness).

## Source and licence

Data: Software in Partnership Ltd (SiP), published by Derek M. Jones,
<https://github.com/Derek-Jones/SiP_dataset>.
Paper: D. M. Jones and S. Cullum, *A conversation around the analysis of the SiP effort
estimation dataset*, arXiv:1901.01621.

Licence: [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/):
non-commercial use only, credit the source, do not publish modified copies of the data.
Every report or slide that uses these numbers must credit SiP / Derek M. Jones.
