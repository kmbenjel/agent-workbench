# Footprint comparison (run on a 32-vCPU cloud VM, Ubuntu 24.04, 2026-11-13)

Reproduce: `python bench/compare.py` (needs `uv`; installs into throw-away venvs with a private cache, so install
times are cold-cache and include the download). `label-studio` 1.23.2, `argilla` 2.8.0 (the PyPI client only: its
server needs Docker, Elasticsearch/OpenSearch or a Hugging Face Space), `agent-workbench` 0.1.0.

| tool | install time | disk | packages | time to first page | server RSS |
|---|---|---|---|---|---|
| agent-workbench | 0.1 s | 0.3 MB | 1 | 0.3 s | 25 MB |
| label-studio | 2.5 s | 810 MB | 158 | 33.0 s | 392 MB |
| argilla (SDK only) | 0.6 s | 337 MB | 38 | n/a | n/a |

"Time to first page": from the start command to the first HTTP 200 of the tool's own UI. For agent-workbench the
page already holds cards (`awb demo`); for Label Studio it is the empty UI (first start runs the database
migrations; a project, a labeling config and tasks still have to be created), so this comparison favours Label Studio.
Install times depend on the network and are shown for completeness; the sizes and package counts do not.
