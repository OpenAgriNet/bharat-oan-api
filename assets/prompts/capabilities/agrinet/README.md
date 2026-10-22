# agrinet capability prompts

Browse **capability → section → language**. Filenames retain the section name for
search and editor tabs. Main `agrinet_<language>.md` templates retain global rules
and the original include order; directory order never controls rendering.

The tool groups in `agents/capability_manifest.py` and the tool/section mappings in
`../sections.json` still control loading. Shared sections have one source, not
copies per capability. Folder ownership does not change tool permissions.

## Capabilities

- [schemes](schemes/README.md) — 3 tools
- [amul](amul/README.md) — 1 tool
- [maha](maha/README.md) — 1 tool
- [pmkisan](pmkisan/README.md) — 5 tools
- [pmfby](pmfby/README.md) — 6 tools
- [soil_health](soil_health/README.md) — 1 tool
- [smam](smam/README.md) — 1 tool
- [aif](aif/README.md) — 4 tools
- [kcc](kcc/README.md) — 3 tools
- [khc](khc/README.md) — 5 tools
- [crop_advice](crop_advice/README.md) — 5 tools
- [weather](weather/README.md) — 2 tools
- [location](location/README.md) — 2 tools
- [mandi](mandi/README.md) — 2 tools
- [fertilizer](fertilizer/README.md) — 2 tools
- [seeds](seeds/README.md) — 3 tools
- [agristack](agristack/README.md) — 3 tools

## Language coverage

Supported prompt languages: `as`, `bn`, `en`, `gu`, `hi`, `kn`, `mai`, `ml`, `mr`, `or`, `pa`, `ta`, `te`.

“Localized files” means separate language assets, not a translation-quality score.
“Common” means the same instruction source is used for every language.

| Section | Coverage |
|---|---|
| [amul/amul_and_scheme_disambiguation](amul/amul_and_scheme_disambiguation/) | Localized files, all 13 languages |
| [_shared/grievances](_shared/grievances/) | Localized files, all 13 languages |
| [_shared/insurance_and_loan_eligibility](_shared/insurance_and_loan_eligibility/) | Localized files, all 13 languages |
| [agristack/rules](agristack/rules/) | Common English source, all 13 languages |
| [aif/status](aif/status/) | Localized files, all 13 languages |
| [fertilizer/recommendations](fertilizer/recommendations/) | Localized files, all 13 languages |
| [kcc/status](kcc/status/) | Localized files, all 13 languages |
| [khc/rules](khc/rules/) | Common English source, all 13 languages |
| [maha/schemes](maha/schemes/) | Localized files, all 13 languages |
| [mandi/prices](mandi/prices/) | Localized files, all 13 languages |
| [pmfby/grievance_verification](pmfby/grievance_verification/) | Localized files, all 13 languages |
| [pmfby/payment_issues](pmfby/payment_issues/) | Localized files, all 13 languages |
| [pmfby/status](pmfby/status/) | Localized files, all 13 languages |
| [pmkisan/instalments](pmkisan/instalments/) | Standalone files only: en, hi, mai, or, pa |
| [pmkisan/status](pmkisan/status/) | Localized files, all 13 languages |
| [pmkisan/validation](pmkisan/validation/) | Localized files, all 13 languages |
| [schemes/catalog](schemes/catalog/) | Localized files, all 13 languages |
| [schemes/eligibility](schemes/eligibility/) | Localized files, all 13 languages |
| [schemes/related_videos](schemes/related_videos/) | Localized files, all 13 languages |
| [seeds/availability](seeds/availability/) | Localized files, all 13 languages |
| [smam/status](smam/status/) | Localized files, all 13 languages |
| [soil_health/report](soil_health/report/) | Localized files, all 13 languages |
| [soil_health/status](soil_health/status/) | Localized files, all 13 languages |
| [weather/agromet](weather/agromet/) | Localized files, all 13 languages |
| [weather/forecast](weather/forecast/) | Localized files, all 13 languages |

KHC and AgriStack previously had 13 wrappers each around the same English source;
`*_common.md` now exposes that fact directly. PM-KISAN instalments have no standalone
section in the other eight languages; that inherited difference is preserved, not
filled with an implicit English fallback. Crop advice and location have no extracted
sections. Weather and IMD agromet also keep a short row in the main routing table.

## Editing and checks

Edit a section in its language file; keep shared content in one place. Do not
change the tool allowlist, grouping, or include order as a side effect of file moves.
Markdown trailing blank lines are significant: Jinja strips one final newline from
each template. The fragments retain the original rendered separators without
whitespace-marker comments. KHC/AgriStack common sources, where present, are included
directly rather than through per-language wrappers.

Run the permanent structure and capability tests:

```bash
python -m pytest tests/test_prompt_structure.py tests/test_capabilities.py -q
```

For a structural migration, choose the Git revision immediately before the change:

```bash
PROMPT_COMPARE_REF=<revision> python -m pytest tests/test_prompt_structure.py -q
```

This compares full prompts, no preloads, each capability, each individual tool,
and lazy-loaded instructions in every language, including whitespace. No frozen
hash files are needed. Without an explicit revision, only the Git-comparison cases
are skipped; permanent inventory and behavioral checks still run.
