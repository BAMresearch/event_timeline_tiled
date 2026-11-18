# Presets for this deployment

YAML preset files (`*.yaml`) placed here are loaded by the adapter at start,
besides the shipped `instruments` preset. The format is described in
`docs/technical/presets.md`. To keep them elsewhere, set `ETT_PRESETS` in
`deploy/.env` to that directory; `ETT_DEFAULT_PRESET` chooses the preset the
dashboard starts with. A file that does not load stops the adapter, with the
file, line and column of the problem in its log.
