# Source synchronization — 2026-09-24

Current source version: **2.2.07**; standard distribution: **2.2.07c**.

Repository renamed to **Markida/Taiwan_Patent_Debugging_System**. Product/executable names remain Saint-Island_Patent_MDS.

## Included progress

- Patent-document rules, claim parsing, component-name and quantity handling, custom blacklists/whitelists, error highlighting, document reminders, and drawing-reference checks.
- OCR three-step workflow, background operations, complete-label recognition, prime labels, manual correction/search, rotation/zoom, figure mapping and local result persistence.
- Dedicated implementation-paragraph/drawing comparison and document symbol-list transfer.
- Pixel-cat assistant with contextual next-step choices, animation and user controls.
- Internal collaboration/game features and isolated standard-edition packaging.
- Figure-heading annotation, training/evaluation utilities and an explicitly enabled trial-model packaging option. Trial status and artifact hash checks remain intact.
- Taiwan-to-China conversion in its **September 22 restored stable state**: dependent claims are inserted between existing disclosure and beneficial effects; previous D01–D50 redesign modules are not reintroduced. The dictionary uses the restored shared-location override/cache/fallback mechanism, not the abandoned executable-adjacent-only design.

## Source versus existing installers

September 21 release notes and packages remain historical records. In particular, the September 22 converter rollback changed source after those packages were built. This GitHub synchronization does not recreate installers or publish model weights, and does not change the version number.

## Public-repository boundary

Included: source, tests, synthetic DOCX input fixture, application templates/assets, training utilities and development notes.

Excluded: actual patent training pages, annotations/dataset reports, model weights, local profile/chat state, generated DOCX output, company update archives and local QA logs. No company shared storage is read or modified by this synchronization.

Use `python tools/run_regression.py` to run isolated source regression. Dataset-specific integration checks explicitly report skipped when their private datasets are absent; they remain enabled in a complete local research workspace.

## Synchronization verification

- Full local workspace: 1,060 unittest cases passed, no skips.
- Git-index-only export (no private models, datasets or QA files): 1,055 passed and 5 explicitly skipped for unavailable private inputs; no failures or errors.
- Source whitespace check and staged artifact/common credential-pattern scan passed.
- OCR UI tests now use controlled placeholder model paths for mocked workers/model-selection checks instead of relying on unpublished local model files. Runtime inference behavior was not changed by this synchronization.
