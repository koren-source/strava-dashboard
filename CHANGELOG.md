# Changelog

## [0.1.0.0] - 2026-09-12

### Fixed

- Keep complete activity history in the private dashboard so weekly totals survive the public recent-ride window.
- Preserve corrections and source absence through checked, repeatable complete captures.
- Fail incomplete source reads and private uploads before publishing a new public snapshot.

### Changed

- Keep full activity payloads outside the public repository and bound optional detail reads during historical collection.
