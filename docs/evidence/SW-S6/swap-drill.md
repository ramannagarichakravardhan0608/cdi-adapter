# SW-S6 component swap drill

Golden set: ARCHITECTURE §8.2 (Anjali Das). Run 2026-10-14T12:38:26+00:00.

**PASSED** - not complete: object_store, job_queue, drive_connector, sign_in not run here

| Swap point | Default | Alternative | Result |
|---|---|---|---|
| pdf_renderer | pypdfium2 | (none) | matched |
| object_store | s3 | fs | not run: ConnectionError: no S3 store answers at http://127.0.0.1:9000 - the filesystem store alone was checked: every byte read back as saved |
| terminology | internal canonical vocabulary (no licence) | SNOMED CT + ICD-10 (licensed) | differences explained |
| job_queue | redis 7.2 | valkey | not run: no Redis and Valkey servers named (--redis-url, --valkey-url); the drill needs the real servers |
| drive_connector | local folder | OneDrive / SharePoint / Google Drive | not run: needs a real account of each drive; the contract test runs in CI (tests/test_ls_s1_acceptance_unit.py) |
| sign_in | local accounts | OpenID Connect | not run: needs a real identity provider; the contract test runs in CI (tests/test_ent_s2_acceptance_unit.py) |

## terminology: each difference

- condition: Type 2 diabetes mellitus - code: 'condition:type-2-diabetes-mellitus' / '44054006' - explained: the same: a SNOMED CT code with the licence, the internal id without it
- condition: Type 2 diabetes mellitus - code_system: 'https://careflow.clinic/term' / 'http://snomed.info/sct' - explained: SNOMED CT is used only where it is licensed; without the licence the term is named by the internal vocabulary's own id (story NFR1, AC3)
- condition: Type 2 diabetes mellitus - icd10: None / 'E11' - explained: ICD-10 is given only where it is licensed
- condition: Hypertension - code: 'condition:essential-hypertension' / '59621000' - explained: the same: a SNOMED CT code with the licence, the internal id without it
- condition: Hypertension - code_system: 'https://careflow.clinic/term' / 'http://snomed.info/sct' - explained: SNOMED CT is used only where it is licensed; without the licence the term is named by the internal vocabulary's own id (story NFR1, AC3)
- condition: Hypertension - icd10: None / 'I10' - explained: ICD-10 is given only where it is licensed
- medication: Tab Metformin 500 mg - code: 'medicine:metformin' / '372567009' - explained: the same: a SNOMED CT code with the licence, the internal id without it
- medication: Tab Metformin 500 mg - code_system: 'https://careflow.clinic/term' / 'http://snomed.info/sct' - explained: SNOMED CT is used only where it is licensed; without the licence the term is named by the internal vocabulary's own id (story NFR1, AC3)
- medication: Tab Amlodipine 5 mg - code: 'medicine:amlodipine' / '386864001' - explained: the same: a SNOMED CT code with the licence, the internal id without it
- medication: Tab Amlodipine 5 mg - code_system: 'https://careflow.clinic/term' / 'http://snomed.info/sct' - explained: SNOMED CT is used only where it is licensed; without the licence the term is named by the internal vocabulary's own id (story NFR1, AC3)
- medication: Tab Atorvastatin 10 mg - code: 'medicine:atorvastatin' / '373444002' - explained: the same: a SNOMED CT code with the licence, the internal id without it
- medication: Tab Atorvastatin 10 mg - code_system: 'https://careflow.clinic/term' / 'http://snomed.info/sct' - explained: SNOMED CT is used only where it is licensed; without the licence the term is named by the internal vocabulary's own id (story NFR1, AC3)
- allergy: Penicillin - code: 'allergen:penicillin' / '373270004' - explained: the same: a SNOMED CT code with the licence, the internal id without it
- allergy: Penicillin - code_system: 'https://careflow.clinic/term' / 'http://snomed.info/sct' - explained: SNOMED CT is used only where it is licensed; without the licence the term is named by the internal vocabulary's own id (story NFR1, AC3)
