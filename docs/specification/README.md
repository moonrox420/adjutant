# Authoritative product specification

These files were copied byte-for-byte from the user-supplied
`C:\Users\droxa\Documents\PRD\adjutant-prd` directory. `provenance.json` records the original
names, repository names, and SHA-256 hashes. They define the full requested scope: 199 engineering
tickets, not just the currently implemented local campaign workflow.

The SQL files are original design references, not new migrations. Do not execute them against an
existing workspace: use the checked migrations in the repository's `migrations` directory.

The runtime event registry derives from the 46 source contracts. Runtime registry 1.1 keeps the
prior event-name envelope compatible and raises `brand.kill_switch.engaged` to event version 2
for its stricter reason-length constraint. Future event contract changes are checked by
`scripts/check_event_contracts.py`; implemented producers currently cover only a subset of events.

The full product is not complete. `BUILD_STATUS.md` records actual implemented functionality,
verification results, and remaining systems. Green local tests do not constitute cloud deployment,
advertising-platform approval, a paid-campaign canary, or completion of the source release gates.
