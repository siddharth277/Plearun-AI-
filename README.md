# Plearun verification CLI

`python3 plearun_cli.py REPORT RECORDS_DIR [-v] > findings.json` — standard library only, offline. `make test` runs 33 tests (`python3 -m unittest discover -s tests` if `make` is missing).

## How it works
1. **Load** (`loaders.py`): one loader per format (camera/notes CSV, dispatch JSON, VTT). Handles BOM, marker lines, CRLF, UTF-8/UTF-16/cp1252, quoted multi-line fields. Each row keeps its exact raw text.
2. **Times** (`timeparse.py`, `clocks.py`): epoch s/ms, ISO with/without offset, `HH:MM`, VTT-relative → one scale. Stamps with no timezone use the most common offset seen in the data; time-only stamps use the incident date.
3. **Claims** (`claims.py`): regex extraction of speeds, event times, "N minutes after X", counts. Anything else is `not_checkable`.
4. **Clock alignment** (`clocks.py`): dispatch is the reference. Another device is aligned only if **≥2 different shared events** (e.g. 911 call, dispatch, on scene, a spoken/logged speed) give offsets that agree within stamp resolution, and those form a majority of its candidate anchors. Otherwise it stays unaligned and its times are never compared with other devices. Same-device differences need no alignment.
5. **Decide** (`matcher.py`): match/conflict need an aligned, in-window row; ambiguity gives `not_settled`.
6. **Verify** (`verify.py`): every cite is re-looked-up and the quote must be an exact substring of that row's raw text, else the finding is downgraded to `not_settled`.

`row` = 1-based index of data records (CSV after marker+header), dispatch events in document order, VTT cues in order.

## Three hardest decisions
- **Refusing to align on one anchor.** Sample: notes share only one event with dispatch, camera/bodycam none, so those clocks stay unaligned. Costs recall (stop, on-scene, speed → `not_settled`) but avoids a false conflict: the only 58 mph reading is 7 minutes before the claimed time, which looks like a conflict but is probably clock skew.
- **Speed conflicts need a clear gap.** Hedged times ("about 21:14") allow a conflict only if every reading differs by ≥8 mph; 0 mph is treated as stopped/dropout.
- **Conflict threshold is wider than match threshold** (3× tolerance, min 5 min), so borderline gaps are `not_settled`.

## Known limits
- Claims mixing checkable and uncheckable parts ("at 21:14 near mile marker 4") are judged on the time only. The event is the keyword nearest the time ("stopped at 21:16 and a unit arrived" = the stop); clauses with two times are split; "9:14 PM" is converted.
- Relative claims with >100k subject×reference rows are `not_settled` (a pair scan would hang on big notes files).
- Dispatch is assumed to be true time; keyword labelling of notes/transcripts can mislabel an event.
- Two corrupted anchors that agree can outvote one good one. Naive stamps in a device that also has tz-aware stamps are assumed local.
- Counts are only checked for 911 calls and units, and only `match` or `not_settled`.

## What breaks first on real data
Report phrasing (regex claims) and event labelling by keyword. Wider vocabularies or an LLM that may only choose among candidate row IDs would be the next step. Reading whole files into memory is fine to ~100s of MB (200k camera rows tested in ~1s).

## Tools used
Claude (AI coding assistant): used to review the code, run the tests and the sample, and tidy the Makefile/README. Earlier design and code generation used Claude (chat). Everything was run locally with Python 3.11 `unittest`. I reviewed and can explain every module.
