# Archived ingestion and geolocation experiments

The supported workflow is the three notebooks under `notebooks/ingestion/`.
These notebooks preserve the original experiments and their saved cell outputs;
they are not prerequisites for the active pipeline. Their original imports/output
paths may need adjustment before replaying an experiment.

- `georgia_ingestion/`: original PDF preparation and project-list notebooks,
  consolidated into `02_georgia_power_ingestion.ipynb`.
- `geolocation_experiments/`: notebooks 04-09, original helper modules and their
  historical checks. Active project matching now lives in `gridlock.geolocation`.
- Reused routes and the endpoint registry are preserved in
  `data/processed/dominion/reference/`. Data used only by retired experiments
  was removed during cleanup; these archived notebooks are not supported entry points.

Notebook 07 supplied ten resolved asset routes plus one route requiring review.
Notebook 09 supplied two additional resolved routes. The active workflow reads
these saved results directly; it does not rerun either graph solver.

Notebook 08's unverified coordinate suggestions remain visible in the archived
notebook. Its unused export files were removed. Those suggestions are not inputs
to active project geolocation.
