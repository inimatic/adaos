# Documentation Language and Translation Policy

Status: normative documentation policy.

Last reviewed: 2026-08-07.

## Decision

English is the authoritative language for AdaOS documentation. Product
definitions, architecture contracts, implementation status, roadmaps,
acceptance evidence, API behavior, and operational instructions are governed by
their English pages.

Russian documentation is a deliberately small translation layer for stable,
public-facing concepts. It is not an alternative specification, an independent
roadmap, or a place for unique normative decisions.

If English and Russian text disagree, the English source is authoritative.

## Russian Coverage Boundary

The maintained Russian translation contains only these paths:

| English authority | Translation path |
| --- | --- |
| [Documentation home](index.md) | `ru/index.md` |
| [Product Model](product/index.md) | `ru/product/index.md` |
| [Solution Directions](product/solution-directions.md) | `ru/product/solution-directions.md` |
| [This policy](documentation-language-policy.md) | `ru/documentation-language-policy.md` |

Architecture, roadmaps, evidence records, CLI and SDK references, detailed
guides, and implementation notes remain English-only. The Russian navigation
links directly to those canonical English sections; it does not generate
English fallback copies under Russian URLs. This keeps language boundaries
visible and prevents an English page from masquerading as a maintained
translation.

The boundary is owned here, in the English source tree. Russian pages must not
define their own coverage rules.

## Translation Contract

A maintained Russian page must:

1. use the same relative path as its English source under `docs/ru/`;
2. identify and link the authoritative English page near the top;
3. preserve the English page's meaning, status distinctions, warnings, and
   non-goals;
4. translate prose, not identifiers, commands, schema names, file paths, or
   compatibility terms that must remain exact;
5. avoid adding claims, examples, plans, or requirements that do not exist in
   English;
6. link back to English-only detail instead of recreating an abridged technical
   specification.

A Russian page may adapt sentence structure and terminology for natural
Russian, but it must not become a summary with a different scope. If a source
page changes too frequently to support a faithful low-maintenance translation,
the Russian file should be removed and the Russian navigation should link
directly to the English page.

Translation discovery belongs to the language selector in the site header.
Pages must not add recurring inline translation notices or maintain
language-by-language link lists in their content.

## Promoting Useful Russian Material

When useful material exists only in Russian:

1. evaluate whether it is current and still belongs in the documentation;
2. move the current, reusable content into an appropriate English authority;
3. link it into the English navigation or authority map when needed;
4. remove the Russian-only normative copy;
5. retire obsolete material instead of translating historical drift into the
   English source.

This preserves useful knowledge without allowing the translation layer to
become a second source of truth.

## Maintenance Workflow

Changes to authoritative English pages should be reviewed for translation
impact. Only changes to the four maintained public-facing pages require a
Russian update. Technical work should normally update English documentation
only.

Strict MkDocs builds, link checks, and UTF-8 checks apply to both the English
source and the maintained Russian translations.
