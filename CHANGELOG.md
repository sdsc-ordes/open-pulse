# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0](https://github.com/sdsc-ordes/open-pulse/compare/v2.0.0...v2.1.0) (2026-07-09)

### Features

* **hub:** admin-managed reader tokens — Users panel, activity log, graph scoping (#211) ([3d09901](https://github.com/sdsc-ordes/open-pulse/commit/3d09901e4265ef528fa494760527f32fd528c77f))
* **infra:** run hub and cli orchestrator rootless as host operator ([7b9d581](https://github.com/sdsc-ordes/open-pulse/commit/7b9d5815ac5b27669b3362289718dfdcfb9c4e07))
* **cli:** add 'activity sync' — weekly GitHub push-activity poller ([95e1966](https://github.com/sdsc-ordes/open-pulse/commit/95e1966eadd206e9de15db1ea2168e1e581cc573))
* **hub:** reader-role hardening + entity-page performance & layout (#196) ([1dabaa7](https://github.com/sdsc-ordes/open-pulse/commit/1dabaa71c6caa4e4b4ddf39f2ebd642c5012cf47))
* **hub:** per-contributor commit counts on the entity page (#195) ([6a7be5f](https://github.com/sdsc-ordes/open-pulse/commit/6a7be5f3bbc450e1e827844bc7f7406d5757ba6b))
* **hub:** identity across organisations — bridge github org ↔ ROR institution (#193) ([1c39bb5](https://github.com/sdsc-ordes/open-pulse/commit/1c39bb5e2c61dfcf18dd03f2328461c1cdc81cfc))
* **hub:** reconcile person/publication identity across all DuckDB stores (#192) ([404dfa5](https://github.com/sdsc-ordes/open-pulse/commit/404dfa5a6ba37326e34395f7d362f5f20fe94d89))
* **hub:** entity page — pin Summary + metrics on top, masonry for the rest (#191) ([7b0f9bf](https://github.com/sdsc-ordes/open-pulse/commit/7b0f9bfe8827d25a878cb6f242902a75a55eeaa4))
* **hub:** label cited works by publication title, DOI as the reference (#190) ([49da69a](https://github.com/sdsc-ordes/open-pulse/commit/49da69ae443c92aa9907722f424e69ba41ad2c9f))
* **hub:** label ORCID authors by name, bridge via githubUsername (RDF) (#189) ([e52388a](https://github.com/sdsc-ordes/open-pulse/commit/e52388ae789c8a076f5b001c07f92719717b822d))
* **hub:** TomSelect named-graph picker — select one or several graphs (#188) ([dd20df8](https://github.com/sdsc-ordes/open-pulse/commit/dd20df808c4323a76b260fcdcb3c87c3c6204048))
* **hub:** unify repo badges into one rendered Badges fact with hover labels (#186) ([91dff8f](https://github.com/sdsc-ordes/open-pulse/commit/91dff8f5556d214cb2f10c1b06c19107b97dbfd0))
* **hub:** render README/profile badges from markdown blobs (#185) ([4f23dbe](https://github.com/sdsc-ordes/open-pulse/commit/4f23dbe8a00fc90f91bf46f104e21399da2533f4))
* **hub:** harmonize contributor identity across Neo4j (github) and RDF (ORCID) (#183) ([8084618](https://github.com/sdsc-ordes/open-pulse/commit/8084618f9fce6c6017df8a9e28d734b24a86e28f))
* **hub:** human-readable release info — drop opaque hashes, link version tags (#182) ([66cf88a](https://github.com/sdsc-ordes/open-pulse/commit/66cf88a7ab03a3457228470b5fffc3e6540e27de))
* **hub:** aggregate multi-value facts into chips + page titles + badges (#181) ([68aea74](https://github.com/sdsc-ordes/open-pulse/commit/68aea745f003c5f54ff15886b62c9c29c1b2b1fa))
* **hub:** card-per-category relations + source-logo values + themed fact cards (#180) ([2566165](https://github.com/sdsc-ordes/open-pulse/commit/2566165452176fb125e2d598df4a5d9281f87308))
* **hub:** readable fact labels + grouped, cross-store relations on the software page (#179) ([927bf64](https://github.com/sdsc-ordes/open-pulse/commit/927bf642badcd51b2282b29a8fcdcddb3f73b246))
* **hub:** upgrade the software (repo) page — CHAOSS, harmonized stores, graph picker (#178) ([ac8c910](https://github.com/sdsc-ordes/open-pulse/commit/ac8c910ed1221666950318a1db7cf2cbc926f946))
* **hub:** add a big warm flagship (Kimi-K2.6) to the chat picker (#177) ([1bad810](https://github.com/sdsc-ordes/open-pulse/commit/1bad81047a3fff589021e18103279fafc0854034))
* **hub:** curate the chat model picker to 5 available models (#175) ([0a3c283](https://github.com/sdsc-ordes/open-pulse/commit/0a3c2836778986201069d16ec994fac333c613c8))
* **hub:** human names on owner + cited-works filter chips (#174) ([9c3e4cc](https://github.com/sdsc-ordes/open-pulse/commit/9c3e4cc7a1af88993a525563a6514e3ba4bff1a4))
* **hub:** multi-select catalog filters that always filter, with human labels (#172) ([f1a1c1d](https://github.com/sdsc-ordes/open-pulse/commit/f1a1c1d92cf3693681068a70d89b041fee0e2536))
* **chaoss:** EPFL-specialized top repositories with All·EPFL toggle (#171) ([c8f5ebc](https://github.com/sdsc-ordes/open-pulse/commit/c8f5ebceeab695340d5ef05b603649fe580bdb1a))
* **hub:** Quests activity panel on the Overview home ([e7dc8a3](https://github.com/sdsc-ordes/open-pulse/commit/e7dc8a3e6f95e09a37f88f02392a3a983acce539))
* **hub:** unify catalog filters into the Filters modal ([b77e47d](https://github.com/sdsc-ordes/open-pulse/commit/b77e47d57cff71c8fb40c5cf3914c54660ff9405))
* **hub:** simplify Try-one to one line + add entity counts ([9b7ce47](https://github.com/sdsc-ordes/open-pulse/commit/9b7ce471f184d4674c594f4862adab9e19a4d689))
* **hub:** expose the OpenPulse API Swagger + link it in the sidebar ([8b8c2da](https://github.com/sdsc-ordes/open-pulse/commit/8b8c2da9742a3371d0ded4b4e62dd8fbb72204ca))
* **hub:** colour-code catalog facets + on-demand refresh ([413d240](https://github.com/sdsc-ordes/open-pulse/commit/413d240062a96d5b7f9849b50afda89f79466f77))
* **hub:** catalog Filters modal — cached top facet values ([04a969b](https://github.com/sdsc-ordes/open-pulse/commit/04a969b88bc455b1f15cbcbcc617f294f02565d1))
* **chaoss:** documentation page — metric × data-source matrix + screenshots ([0eb9392](https://github.com/sdsc-ordes/open-pulse/commit/0eb93920b22d79a9e912b1a391bb09f07d1ceefd))
* **chaoss:** constrain repo picker to indexed repos + comparison overview ([0ac66f8](https://github.com/sdsc-ordes/open-pulse/commit/0ac66f8fc71cc56b047422e9de1e4e408bff43d3))
* **hub:** source page → Sources breadcrumb + table for list-only sources ([3853159](https://github.com/sdsc-ordes/open-pulse/commit/3853159f424a447e1e98ae99c36457ac0253c4cb))
* **hub:** hide Canvas from sidebar; per-session RDF graph picker ([17c8bca](https://github.com/sdsc-ordes/open-pulse/commit/17c8bca5f45354d36bf046e69c5e9aa3b40799c1))
* **hub:** surface RDF citations as linked publication edges ([7c22aab](https://github.com/sdsc-ordes/open-pulse/commit/7c22aabcad713fd3cd861b7010451166da3ac400))
* **hub:** "Present in" panel — show which stores hold an entity ([d0a64a9](https://github.com/sdsc-ordes/open-pulse/commit/d0a64a9825eaa6fe9839a123d6432f55e3bab7c9))
* **hub,chaoss:** simplify examples and point them at SDSC projects ([1056fee](https://github.com/sdsc-ordes/open-pulse/commit/1056fee4ddf27fff504dec7deaec121d3faaae47))
* **hub:** merge catalog into Hub home; move source indices to a Sources section ([9d43486](https://github.com/sdsc-ordes/open-pulse/commit/9d43486c0d179a05ee06791bb8adf8f04bd3d60d))
* **hub:** richer autocomplete search dropdown ([19fa8c3](https://github.com/sdsc-ordes/open-pulse/commit/19fa8c39e11c98f1a8affe0b3706f24abbe22014))
* **hub:** feature SDSC items across types in the catalog ([57b91a4](https://github.com/sdsc-ordes/open-pulse/commit/57b91a4d79cfadc7950139021297de90ef5872be))
* **hub:** DuckDB fallback entity page for items no resolver knows ([da85cb0](https://github.com/sdsc-ordes/open-pulse/commit/da85cb0986fc82858dcf9da0258710153ce3d3b5))
* **hub:** unify RDF + Neo4j on the entity page, tag fact provenance ([a5cf2c4](https://github.com/sdsc-ordes/open-pulse/commit/a5cf2c4700ee858f03970e6f24b24628f5a6dea4))
* **hub:** catalog sort, grid/list views, set featured apart ([f99ecc1](https://github.com/sdsc-ordes/open-pulse/commit/f99ecc14579c6435c35bb8440a9c28917970f6ab))
* **hub:** multi-column, more visual entity detail page ([d7de4ed](https://github.com/sdsc-ordes/open-pulse/commit/d7de4ed8a7aab3ecafae79907ec25ac8c762fd4c))
* **hub:** catalog emojis, richer card metadata, entity hero + resolve cache ([2ea3ce4](https://github.com/sdsc-ordes/open-pulse/commit/2ea3ce4aa2a60ddb3939c9bb8314359d4cfe496a))
* **hub:** themed featured strips in the catalog ([dac6e01](https://github.com/sdsc-ordes/open-pulse/commit/dac6e0189d94acf27daf6ed75d23f39e1219832c))
* **hub:** Catalog — visual, browsable view over the entities ([8e7685a](https://github.com/sdsc-ordes/open-pulse/commit/8e7685a42c60b8333681ed1b99acd64e1e555997))
* **hub:** centralize agent/assistant LLM key + model in Settings ([9b0ddd8](https://github.com/sdsc-ordes/open-pulse/commit/9b0ddd82d37afe8afcf6c4cd103e6d5189b67fc8))
* **agent:** bring-your-own RCP key + dedicated agent env key ([b7258ac](https://github.com/sdsc-ordes/open-pulse/commit/b7258acb7d205c0c0a1ac54e9d53aef63b28e72b))
* **hub:** curate Query-console examples to a research/EPFL-focused set ([f0f1a2e](https://github.com/sdsc-ordes/open-pulse/commit/f0f1a2e94b704759d75bffa9476f8db7e8eea350))
* **hub:** add Cypher example — an organization's top-starred repo ([3a0a050](https://github.com/sdsc-ordes/open-pulse/commit/3a0a0508c8b36a23e6d6a909d2008ebcfb77e9d5))
* **hub:** SDSC + EPFL attribution logos in the marquee ([5789db3](https://github.com/sdsc-ordes/open-pulse/commit/5789db3e256f7b75a6c8c447e4d44cf11cb6a0ab))
* **hub:** refresh script for the read-only DuckDB snapshots the browser needs ([89265e6](https://github.com/sdsc-ordes/open-pulse/commit/89265e6b5ef8c1249fce9cfd66c9e2c9d87943ea))
* **chaoss:** high-activity demo repos + project-card transparency modal ([c8fc38c](https://github.com/sdsc-ordes/open-pulse/commit/c8fc38c877f0671e314ca1e0b8abeea4c1cf15dc))
* **chaoss:** persist project-metric cache to disk (Tier 2) ([63c920c](https://github.com/sdsc-ordes/open-pulse/commit/63c920c51aaa1dfaad6ea7c6641f3f9f550a3a82))
* **chaoss:** weekly cache-warm for project metrics (Tier 1) ([f9b5c9a](https://github.com/sdsc-ordes/open-pulse/commit/f9b5c9a90b3c8c1942d35f271ef3ff71214f15fa))
* **chaoss:** featured-first metrics, rest under a collapsed Advanced section ([691fdb4](https://github.com/sdsc-ordes/open-pulse/commit/691fdb449d1eeb5aec15918c58054f2d0029e961))
* **chaoss:** default to all-time window + label large windows in years ([5a2bdaa](https://github.com/sdsc-ordes/open-pulse/commit/5a2bdaa63af6d6887bd5e2c55109fa023dcfe2ed))
* **hub:** collapse agent tool expanders once the response finishes ([830d678](https://github.com/sdsc-ordes/open-pulse/commit/830d67859a1ca65aa7369a9d7fe062f669a0e74e))
* **hub:** agent chat — fix layout, cap width, restyle messages, add exports ([85569be](https://github.com/sdsc-ordes/open-pulse/commit/85569bee4f1b81c63e5be43e6a5f0e4db7a24d99))
* **hub:** make CHAOSS metric trace code blocks render beautifully ([504d880](https://github.com/sdsc-ordes/open-pulse/commit/504d880528b382410d280da042359cf91185f96f))
* **hub:** remove agent-chat header bar; move actions to composer ([53575bc](https://github.com/sdsc-ordes/open-pulse/commit/53575bc79ca72b94d49770b620a045ef225b09f5))
* **hub:** nicer one-line agent tool-call summary ([865ad50](https://github.com/sdsc-ordes/open-pulse/commit/865ad50e1b1a22ed09d4fb20c7b363e5a1556e27))
* **hub:** tidy agent-chat layout — drop quick-example chips, pin header + composer ([d8609fe](https://github.com/sdsc-ordes/open-pulse/commit/d8609fe80857b6f6b084276607bb43d9bd869a5d))
* **hub:** centered+blurred query-console modals; move Examples/Snapshot into the saved-queries row ([6dee75e](https://github.com/sdsc-ordes/open-pulse/commit/6dee75e23514deeddacce8b14220567347e62fed))
* **hub:** query console — Examples & Snapshots as modals (free the rails' space) ([3d9eb18](https://github.com/sdsc-ordes/open-pulse/commit/3d9eb18cd438cfb4e1093a8a75b90cd926f457fa))
* **hub:** agent chat — conversation history + customizable tool-chaining depth ([a9f1580](https://github.com/sdsc-ordes/open-pulse/commit/a9f158083aea7ad84732a5fb4c826b355ef81b07))
* **hub:** agent chat UX — tool tables/queries render, settings modal, fit-to-height ([da9f8e5](https://github.com/sdsc-ordes/open-pulse/commit/da9f8e59d6da2c671b1e04bbe0edc64d49ebd347))
* **quest:** run-quests live in a gitignored data/quests exchange folder ([d01136f](https://github.com/sdsc-ordes/open-pulse/commit/d01136fa72045a957c0c4c0590a6fefc29a2d138))
* **pipeline:** extract crawled users/orgs, not just repos ([5c6c2be](https://github.com/sdsc-ordes/open-pulse/commit/5c6c2beca599d1e0961824aa06d2753c42438168))
* **hub:** default-hide the `raw` column in the row browser ([d13cf46](https://github.com/sdsc-ordes/open-pulse/commit/d13cf4689d66df289783a38740b26f695ab5b544))
* **hub:** consume the GME federated index manifest for Sources tiles ([3b3412e](https://github.com/sdsc-ordes/open-pulse/commit/3b3412ef6f9c6af4ce4921757f7ce5abda0df97e))
* **hub:** pin DataScience GitLab logo to the SDSC webclip asset ([007ca7f](https://github.com/sdsc-ordes/open-pulse/commit/007ca7f2f749da301248b1e27c18406161dd3490))
* **hub:** surface GitLab user stores on the grid + SDSC logo for DataScience ([80e9b92](https://github.com/sdsc-ordes/open-pulse/commit/80e9b92abbd0d428b60983dbfbce66f783465a84))
* **hub:** register new indices (DockerHub, GitLab, HF papers) — tables + logos ([0027c1f](https://github.com/sdsc-ordes/open-pulse/commit/0027c1f5f9134b03aa8f533d27e5fcf32f9848a7))
* **infra:** stage gimie-api sidecar + new GME develop knobs ([13df6d4](https://github.com/sdsc-ordes/open-pulse/commit/13df6d4bb953dc1415f4d252f565456ad03d67b4))
* **chaoss:** add Release Frequency from gme-internal release scalars ([55aad6c](https://github.com/sdsc-ordes/open-pulse/commit/55aad6c59a353395bcfbc7a4685786575b6e7817))
* **chaoss:** read test_coverage from the gme-internal graph triple ([8d0ca4f](https://github.com/sdsc-ordes/open-pulse/commit/8d0ca4fba1a681a19f385e4fd062835733d2d44f))
* **chaoss:** add Test Coverage from the GME README cards ([dcae02d](https://github.com/sdsc-ordes/open-pulse/commit/dcae02d838943afaf2a9e2ddb1aec036504705a6))
* **hub:** agent tool — CHAOSS community-health metrics ([0f7c140](https://github.com/sdsc-ordes/open-pulse/commit/0f7c1402ddaa7d455b9c92aa6eac3e6e05553fc9))
* **hub:** agent tools — GME federated search, extract/gimie, crawler ([34583c1](https://github.com/sdsc-ordes/open-pulse/commit/34583c10036227d2ed85bccaa9d1e77c262803dc))
* **hub:** agent chat — slash commands, file/context attach, tool & model picker ([ad8d424](https://github.com/sdsc-ordes/open-pulse/commit/ad8d4248f1288dedf656a32706cfb160cc759118))
* **hub:** Agent chat — full-page assistant with tools + rich rendering ([6e55a9b](https://github.com/sdsc-ordes/open-pulse/commit/6e55a9bee840ce1e66bbe62e4f6efabcea08f25c))
* **chaoss:** add Committers and Issue Response Time ([27a5bea](https://github.com/sdsc-ordes/open-pulse/commit/27a5beaaa795f44a4fcc887a60b7cb01218a08eb))
* **chaoss:** add Upstream Code Dependencies, Docs Discoverability, License Coverage ([3289a5f](https://github.com/sdsc-ordes/open-pulse/commit/3289a5fbcf9d226e0aa3733603e6b76de2f0f620))
* **chaoss:** project-scoped metrics API + 3-bucket catalogue ([fc5d001](https://github.com/sdsc-ordes/open-pulse/commit/fc5d001f5c5f1d2a54ee5023ccf054ba1f811847))
* **hub:** chart views (D3) for the query console results ([71ebae7](https://github.com/sdsc-ordes/open-pulse/commit/71ebae7be267dc2b7552e90f049576ffe235f85a))
* **hub:** lazy-load Sources grid so /hub paints instantly ([0199647](https://github.com/sdsc-ordes/open-pulse/commit/01996475c1f7e05774a5c4c27313918b0a006d99))
* add protein-AI ecosystem quest config ([5e588fd](https://github.com/sdsc-ordes/open-pulse/commit/5e588fd4cc9cba4dbea0121c0fed1ef3b5aee004))
* **hub:** browse split stores, all columns, cross-table links, hide columns ([2202867](https://github.com/sdsc-ordes/open-pulse/commit/220286714585450e48b64e2c37be863ceae48b2b))
* **hub:** read DuckDB stores from the GME .ro.duckdb snapshot ([014175f](https://github.com/sdsc-ordes/open-pulse/commit/014175fdc6c462c664c9c341a5bab27857952f52))
* **infra:** GME index reindex runbook + clear/rebuild script ([a991ded](https://github.com/sdsc-ordes/open-pulse/commit/a991dedf1f181fc0e263f93635501105e99c6148))
* **hub:** surface 5 more DuckDB tables from the restructured index ([3dae3a0](https://github.com/sdsc-ordes/open-pulse/commit/3dae3a0e7ba83f5fbea9d5122af484678814c985))
* **neo4j:** preserve subkind + platform on multi-platform v3 nodes ([43bc67a](https://github.com/sdsc-ordes/open-pulse/commit/43bc67aa77858672e1a889e6dbb2294fb49ed9f9))
* **neo4j:** URL-normalise crawler graph identifiers for v3 ingestion ([fadacb5](https://github.com/sdsc-ordes/open-pulse/commit/fadacb52f44471c97172605363138e61263ca5b3))

### Bug Fixes

* **hub:** suppress publiccode content-hash and dedup repo facts ([e3809a4](https://github.com/sdsc-ordes/open-pulse/commit/e3809a4b0123ce682d9a96f6635c4fcc899d6e0c))
* **hub:** resolve contributor/owner edges across all graphs, not the pinned one (#187) ([9084713](https://github.com/sdsc-ordes/open-pulse/commit/9084713d698ad9b0bec8d5ae3f3fb28b1ea38251))
* **hub:** date-only timestamps + render avatar/badge images on the entity page (#184) ([a507f59](https://github.com/sdsc-ordes/open-pulse/commit/a507f59c5f03337b051fdbccfc5399769dbe3237))
* **hub:** restrict chat picker to the always-resident (24/7) models (#176) ([b5c4d88](https://github.com/sdsc-ordes/open-pulse/commit/b5c4d880605dec2944b83592df32c4c87711ebb7))
* **hub:** match a github org to the Neo4j Org node (not the stray User) ([554b1f5](https://github.com/sdsc-ordes/open-pulse/commit/554b1f5e55ad34b2b413ac07d1210f9958f47d9a))
* **hub:** remove the catalog featured strip ([f359370](https://github.com/sdsc-ordes/open-pulse/commit/f359370da541ccbcf4bc2062b82b11bcd8e75503))
* **hub:** drop the catalog's filter box — one search bar on the home ([4e38161](https://github.com/sdsc-ordes/open-pulse/commit/4e3816164b023966ce84880f06c8782de7721b99))
* **hub:** widen catalog Filters modal; wrap long facet labels ([175bd66](https://github.com/sdsc-ordes/open-pulse/commit/175bd6686404fd50422a40c6db637c8104086b4a))
* **hub:** drop the flaky Qdrant row from the "Present in" panel ([f4dc748](https://github.com/sdsc-ordes/open-pulse/commit/f4dc748d70a29d8b8db284c7a5826f8aea1216b3))
* **hub:** make Qdrant presence robust to a cold store ([92ac072](https://github.com/sdsc-ordes/open-pulse/commit/92ac072af7b86593bc4f4b421857ed596ff4b220))
* **hub:** add '&' between the SDSC and EPFL logos in the marquee credit ([d1def80](https://github.com/sdsc-ordes/open-pulse/commit/d1def8084bf6cbb9e9e811cc8fde5bddb13b3bd4))
* **agent:** self-heal a stale saved model so the agent keeps working ([4f5abf2](https://github.com/sdsc-ordes/open-pulse/commit/4f5abf278066cb2941054e0684fb73abeb07522a))
* **agent:** vega-lite charts with width:container render 0-wide (invisible) ([6c0d0c8](https://github.com/sdsc-ordes/open-pulse/commit/6c0d0c8ecf43a12144ec509502905f9bcd43ab34))
* **quest:** error on missing quest config instead of silent default ([77e6cc9](https://github.com/sdsc-ordes/open-pulse/commit/77e6cc9c4d01fbe26bff61a475a3489961952b39))
* **chaoss:** PR/merge metrics read github-pull_enriched with correct fields ([7f89ec4](https://github.com/sdsc-ordes/open-pulse/commit/7f89ec40ec8a98fe88ae2c0ba68b2cda0d67225c))
* **agent:** render force-directed Vega graphs (charge -> nbody) ([9906f40](https://github.com/sdsc-ordes/open-pulse/commit/9906f40a1578793983372dff450d1a8ce587b057))
* **chaoss:** read response time from time_to_first_attention with fallback ([c08c853](https://github.com/sdsc-ordes/open-pulse/commit/c08c8539d8275b9fead6701edb7f9b570f8b630c))
* **agent:** SSE keepalive heartbeats so slow tool calls don't 502 ([812c671](https://github.com/sdsc-ordes/open-pulse/commit/812c671744604ce7e1cda949b09dc0b2bfe9f04c))
* **agent:** give run_opensearch the real index list (stop hallucinated indices) ([201f8eb](https://github.com/sdsc-ordes/open-pulse/commit/201f8eb14b57b67aa69176a22339e1694c001963))
* **hub:** CHAOSS code blocks — gap between line numbers and code, no inner scroll in modal ([2822a1f](https://github.com/sdsc-ordes/open-pulse/commit/2822a1fb55a657d0b3ccc6d96dc1d1c320398ab3))
* **hub:** run agent tool calls off the event loop (fixes 502 during agent queries) ([08012f7](https://github.com/sdsc-ordes/open-pulse/commit/08012f73f06a6ab9f199004704c7b50aa20e602e))
* **hub:** pin agent composer without :has(); collapse lingering tool-call turn ([ea6f8a2](https://github.com/sdsc-ordes/open-pulse/commit/ea6f8a2b444e2671d35afaeb6a23a775fe91efe4))
* **hub:** make the agent model picker a real dropdown ([e70aa4b](https://github.com/sdsc-ordes/open-pulse/commit/e70aa4b00675ab65b43372fcdaf14801e10505a7))
* **infra:** raise extractor mem limit for the heavier develop image ([39d540d](https://github.com/sdsc-ordes/open-pulse/commit/39d540d7072e18b6730f60fb1e706ca66cd04c98))
* **pipeline:** wire force_refresh to the v2 extract refresh param ([a66d712](https://github.com/sdsc-ordes/open-pulse/commit/a66d712f8ba32e9242563f7deeac276ec77fd0bc))
* **infra:** correct sparql-proxy and valkey healthchecks ([eb31e17](https://github.com/sdsc-ordes/open-pulse/commit/eb31e17685e36a1e3f9f1193c4de9f595d11e3bb))
* **hub:** point overview DuckDB card at the live index stores ([af24d63](https://github.com/sdsc-ordes/open-pulse/commit/af24d639ddbe9fe3b279d21d26cee85a2cc081cf))
* **hub:** URL-key the remaining WITH-variable Cypher example ([ba4097f](https://github.com/sdsc-ordes/open-pulse/commit/ba4097fee2b49e32e32828eb8fce64a044c548c9))
* **hub:** URL-key the Cypher examples + add platform/subkind/frontier views ([59bcc05](https://github.com/sdsc-ordes/open-pulse/commit/59bcc051bc8c9d24172b4e281728f1be324149bf))

### Documentation

* **reference:** document admin/reader access-control model ([9bb2d3e](https://github.com/sdsc-ordes/open-pulse/commit/9bb2d3e87d6f775ab55ba75d012c5ebd7b8979b4))
* remove .env wizard button from landing, link it from Getting Started ([664a190](https://github.com/sdsc-ordes/open-pulse/commit/664a190213b174c69595f0dc1aea9dc6774a82c9))
* **catalog:** design spec for the hub Catalog section ([a9fde61](https://github.com/sdsc-ordes/open-pulse/commit/a9fde6189beb105947ee2e5f02ce7b996132a02d))
* restructure docs-site, refresh outdated content, professionalize tone ([3d6aa45](https://github.com/sdsc-ordes/open-pulse/commit/3d6aa4582357703e52fe390c6f2dacd7f53f56a5))
* **chaoss:** document project-metric caching, weekly warm, and env knobs ([31fbf8c](https://github.com/sdsc-ordes/open-pulse/commit/31fbf8c75e7da9ed7895624cf0c4d4eda200851f))
* GME ask — persist repo signals as index columns for CHAOSS ([abade77](https://github.com/sdsc-ordes/open-pulse/commit/abade776fcb024103ab6e030d58948d80e50839f))
* GME field report + ROR disambiguation plan ([fcea592](https://github.com/sdsc-ordes/open-pulse/commit/fcea592129f0a879a11f419809331d71e6fd5f7c))
* proposal for GME read-only DuckDB snapshots (lock contention) ([f73fab3](https://github.com/sdsc-ordes/open-pulse/commit/f73fab3c6fcbd533446fad5d59e8047eefbc150e))

### Chores

* sync __init__ __version__ via release-please extra-files ([bc03fc3](https://github.com/sdsc-ordes/open-pulse/commit/bc03fc38dcf56719f1d7ca24ac8ee9690dcac99d))
* fix CHANGELOG markdownlint violations (MD004/MD012) ([81d128c](https://github.com/sdsc-ordes/open-pulse/commit/81d128cd793d71e13744c5bc9bd3dcd0a05f098a))
* ignore dev/ working notes and untrack committed proposals ([cbea3c4](https://github.com/sdsc-ordes/open-pulse/commit/cbea3c426189c602c82439d4681204ce673c6d25))
* add pending quest configs, enrichment plan, and neo4j backfill script ([cd70165](https://github.com/sdsc-ordes/open-pulse/commit/cd701657b21fc3ba020c01ed9d96dbe319435744))
* **quest:** reextract-2026-06 full-run settings (max_workers=6, isolated named graph) ([a4bd101](https://github.com/sdsc-ordes/open-pulse/commit/a4bd101a5a488d17dbd00e04d08d0e162e13277d))

## [1.0.1](https://github.com/sdsc-ordes/open-pulse/compare/v1.0.0...v1.0.1) (2026-05-22)

### Documentation

* escape literal pipes in nodes schema tables (markdownlint MD056) ([a22af39](https://github.com/sdsc-ordes/open-pulse/commit/a22af3999a1e866345b55ee294216ad68c4f1b8f))
* **landing,docs:** static node builder app + cross-links + footer date ([b4e5f0a](https://github.com/sdsc-ordes/open-pulse/commit/b4e5f0ad7191520a9fc4b9726f8f6c3a4055a10b))
* **landing,site:** logos in node tabs, minimal color blocks, consistent panel size ([665d8fc](https://github.com/sdsc-ordes/open-pulse/commit/665d8fc07e8068a815b40c925ac548f0803e575e))
* **landing:** drop 'Public soon' sticker, pill-style install tabs, add .env wizard CTA under Docs button ([1e7c5ad](https://github.com/sdsc-ordes/open-pulse/commit/1e7c5add1fa56833ccd339ecfd3886515a1d9271))
* **landing:** install tabs + Docs button + space-styled wizard + Docusaurus at /docs/ ([48584ee](https://github.com/sdsc-ordes/open-pulse/commit/48584ee880aebaecff6f9bc0427cfddfca985b1e))
* **landing:** install tabs + Docs button + space-styled wizard + Docusaurus at /docs/ ([ab2fcab](https://github.com/sdsc-ordes/open-pulse/commit/ab2fcabef71fed33eae488d5e3c4cc6a8c88440a))
* **landing:** self-deploy leaf bodies — description, prereqs, follow-up and next link ([9278f8b](https://github.com/sdsc-ordes/open-pulse/commit/9278f8b0a1d8d1f49607cfcb8899efccaea9257a))
* **landing:** split 'hosted instance' vs 'self-deploy' + YAML-driven nodes registry ([290a417](https://github.com/sdsc-ordes/open-pulse/commit/290a4170487823d8199d501952352a7cea53d3f8))
* **landing:** super-tabs + left-rail leaf-tabs for Nodes / Self-deploy ([e3cd46f](https://github.com/sdsc-ordes/open-pulse/commit/e3cd46fd9d2069c688735bd80e33be9566633df3))
* **site:** align Docusaurus theme with the landing palette ([864c57e](https://github.com/sdsc-ordes/open-pulse/commit/864c57eb7c1839ed9b09da5916dd545f303cc18b))

## [Unreleased]

### Changed

* Consolidated the entire Open Pulse stack under `infra/open-pulse-stack/`. The main compose, the CLI orchestrator overlay, and the full GrimoireLab compose now live alongside each other as `docker-compose.yml`, `docker-compose.cli.yml`, and `docker-compose.grimoirelab.yml`; GrimoireLab supporting assets (applier source, config templates, sigils, one-shot scripts) moved to `infra/open-pulse-stack/grimoirelab/`. Updated `deploy.py` path constants, `health.py` `_COMPOSE_FILE`, `justfile` `regen-grimoire-config`, `.github/dependabot.yml`, the docker-validate workflow (with a third matrix entry exercising the grimoirelab compose), and every doc that referenced the old paths.
* Split `.env` along the principle "when launching from `infra/`, all env lives in `infra/`; otherwise `<repo>/.env` is just for the open-pulse tool acting as a client". `<repo>/infra/.env` is the AUTHORITATIVE deployment env: image refs, ports, resource limits, storage paths, ALL container-side credentials, per-service knobs (HUB_AUTH, CRAWLER_*, EXTRACTOR_*, GrimoireLab block, V2/RAG flags). Compose loads only this file (`--env-file infra/.env`); every service additionally `env_file:`-pulls it so any key set there reaches the container without an explicit `environment:` mapping. `<repo>/.env` is the tool/client env, consumed only by the open-pulse Python CLI / hub when running on the host against EXTERNAL infrastructure — compose never reads it. Both files are gitignored; `infra/.env` auto-seeds from `infra/.env.example` on first `op deploy up`, while `<repo>/.env` is a manual copy from `<repo>/.env.example`. `deploy up` / `down` / `ps` and the cli + grimoirelab compose env_file directives all dropped the second `--env-file` / `env_file:` entry.
* Simplified default auth across the stack to `openpulse` / `replace-me`. Where the underlying service forces a specific username (Neo4j → `neo4j`, OpenSearch admin → `admin`, MariaDB init → `root`) only the password changes; everywhere else (GrimoireLab Postgres user, SortingHat superuser, hub login UI) the default username is `openpulse`. Compose-baked fallbacks (`${NEO4J_AUTH:-...}`, `${GRIMOIRELAB_DB_USER:-...}`, `${GRIMOIRELAB_DB_PASSWORD:-...}`) and the runtime fallback in `gui/hub/routes/stats.py` now match. `replace-me` is a placeholder; rotate before any non-local deployment.
* Pinned `OPEN_PULSE_DATA_DIR` to an absolute path during `.env` seeding. The previous default `./data` resolved differently between `op deploy …` (relative to repo root via `--project-directory`) and raw `docker compose -f infra/open-pulse-stack/…` (relative to the compose file), silently splitting state into two locations. `_ensure_env_files` now substitutes the line with the absolute repo-root data path when seeding `infra/.env` from the template; `OPEN_PULSE_HOST_PATH` is filled the same way. `GRIMOIRE_DATA_DIR` derives from `${OPEN_PULSE_DATA_DIR}` so GrimoireLab data lands alongside Neo4j et al. under a single root.
* Hardened `.gitignore` to ignore `data/` recursively (any nesting level) so an accidental relative-path resolution can't pollute the working tree. Removed the per-path `infra/services/{neo4j,portainer}/data/` entries that the recursive rule now covers.
* Added explicit `mem_limit`, `cpus`, and `restart` policies to every service in `infra/compose/docker-compose.yml` and `infra/services/grimoirelab/docker-compose.yml`. Each cap reads from a per-service env var (e.g. `OPENSEARCH_MEM_LIMIT`, `NEO4J_MEM_LIMIT`, `MORDRED_MEM_LIMIT`, `EXTRACTOR_MEM_LIMIT`) so production deploys override the dev defaults without editing the compose. Neo4j heap and page cache are now explicit (`NEO4J_HEAP`, `NEO4J_PAGECACHE`) and sized to fit under the new cap. Mordred default cap dropped from 4g to 2g (it idled at 1.78 GiB; the previous 4g was generous and contributed to the host-OS pressure that OOM-killed opensearch). See `dev/advise/2026-05-05-resource-caps-and-oom-diagnosis.md` for the full diagnosis, budget math, and how to apply the new caps to running services without downtime.
* Added healthchecks to `opensearch-node1` (`_cluster/health?wait_for_status=yellow` with auth — `yellow` is the success state on a single-node deploy because replicas can't be allocated) and `opensearch-dashboards` (liveness via `GET /` — *not* `/api/status`, which requires auth in v3 and would always report unhealthy).
* Simplified Compose topology to a two-file model under `infra/compose/`: `docker-compose.yml` for infra services and `docker-compose.cli.yml` as an optional CLI overlay.
* Updated `deploy` command with a `--with-cli` flag on `up`, `down`, and `ps` to include the CLI overlay file without requiring manual `--file` arguments.
* Updated compose/deploy documentation (`README.md`, `infra/compose/README.md`, `AGENTS.md`) to reflect registry-based CLI container usage (no local build in compose flow).
* Refactored grimoire command surface: replaced top-level `open-pulse grimoire ...` with `open-pulse services grimoire ...` for service actions and `open-pulse gui grimoire` for Streamlit UI.
* Reorganized grimoire implementation modules by responsibility: logic moved to `open_pulse.utils.grimoire` and Streamlit UI moved to `open_pulse.gui.grimoire_streamlit`.
* Refactored integration boundaries by adding `src/open_pulse/services/` as the shared service layer for Neo4j/Tentris clients, service config defaults, health probes, and run-scoped service lifecycle management.
* Updated quest pipeline execution to inject a run-scoped `ServiceContainer` into step context and close all services deterministically at the end of full runs and single-step runs.
* Refactored `open-pulse health` to delegate endpoint probes to `open_pulse.services.health` and source Neo4j/Tentris default endpoints from shared service config constants instead of command-local hardcoded defaults.
* Updated docs and config examples to document the new `quest.services` block and service-layer architecture.
* Updated `Dockerfile-open-pulse` default CMD from legacy `doctor` to `health` to match the new Typer CLI command structure.
* Fixed `Dockerfile-airflow` broken COPY path (`../../src/airflow/...`); commented out the copy as a placeholder since no airflow source exists yet, and bumped base image from Python 3.9 to 3.11.
* Added `pyproject.toml` and `uv.lock` to `docker-validate.yml` path triggers so dependency changes that affect Docker builds are caught by CI.
* Updated `AGENTS.md` with container image details (build context, default CMD, placeholder status) and devcontainer configuration notes, and expanded CI trigger documentation.

* Split monolithic `tests/test_cli.py` into per-domain test modules: `test_cli.py` (entry point), `test_deploy.py`, `test_quest.py`, `test_grimoire.py`, `test_health.py`, and `test_orchestrator.py`. Added `conftest.py` with shared fixtures.
* Added new test cases: `deploy down --volumes` flag pass-through, `deploy down`/`ps` Docker-unavailable guards, `deploy up --file` compose override, `quest start --resume` flag forwarding, `quest start --config` custom path, pipeline failure propagation, grimoire `install-watcher --clone-dir`, mixed-state container health check, orchestrator checkpoint persistence on success and failure, and empty task list handling.

### Removed

* Removed `infra/env/` and the old `infra/services/grimoirelab/` directories. Their contents either moved to the new structure (`infra/services/grimoirelab/docker-compose.yml` → `infra/open-pulse-stack/docker-compose.grimoirelab.yml`; the `applier/`, `config/`, `python-scripts/`, `scripts/`, and `README.md` siblings → `infra/open-pulse-stack/grimoirelab/`) or were superseded (`infra/env/.env.example` → `<repo>/.env.example` + `infra/.env.example`; `infra/services/grimoirelab/.env.dist` → unified `infra/.env.example`).

### Added

* Added `infra/.env.example` (deployment template — comprehensive: every container-side knob the local stack needs) and `<repo>/.env.example` (tool/client template — slim: endpoint overrides + auth for talking to remote infra). The CLI auto-substitutes `OPEN_PULSE_DATA_DIR` and `OPEN_PULSE_HOST_PATH` to absolute paths when seeding `infra/.env`.
* Added `env_file: ../.env` directives to the crawler, extractor, and hub services in `infra/open-pulse-stack/docker-compose.yml` so any per-service knob set in `infra/.env` reaches the container automatically. This is what makes the extractor's V2 / RAG / agent-runtime knobs (V2_*, RCP_TOKEN, OPENALEX_MAILTO, HF_TOKEN, EPFL_GRAPH_*, ORCID_*, INDEX_QDRANT_URL, GRIMOIRE_GITHUB_TOKEN) reach the container without per-key `environment:` plumbing.
* Added new command groups `services` and `gui` with grimoire subcommands split by domain (`services grimoire prepare-config`, `services grimoire install-watcher`, `gui grimoire`).
* Added `open_pulse.utils.grimoire` package (`sparql_config.py`, `cronjob.py`) and `open_pulse.gui.grimoire_streamlit`.
* Added new `open_pulse.services` modules:
  `config.py`, `base.py`, `neo4j.py`, `tentris.py`, `health.py`, and `container.py`.
* Added tests for service-container lifecycle behavior in pipeline runs, step-level service context requirements, service-health probe utilities, and orchestrator `initial_context` propagation.
* Implemented `health` command with Docker daemon check, container status table, endpoint probes (Neo4j HTTP/Bolt, Tentris SPARQL, GrimoireLab DB), smoke tests (CLI version, pipeline config schema, Compose config validation), and rich table output. Exits with code 1 when any check fails. Configurable via `--neo4j`, `--neo4j-bolt`, `--tentris`, and `--grimoirelab-db` options.
* Added health command tests covering Docker unavailable, all-ok scenario, failing endpoints, stopped containers, custom endpoint options, no-containers hint, HTTP/TCP probe unit tests, host:port parsing, smoke test validation, and container status JSON parsing.

* Implemented `grimoire` command group with three sub-commands: `prepare-config` (SPARQL-based GrimoireLab config generator with placeholder query), `ui` (password-protected Streamlit app scaffold for visual config creation), and `install-watcher` (cron job installer for git-based config change detection, Linux/macOS only).
* Added `src/open_pulse/grimoire/` sub-package with `sparql_config.py`, `streamlit_app.py`, and `cronjob.py` modules.
* Added grimoire command tests covering config generation, custom endpoints, Streamlit import guard, watcher installer argument passing, SPARQL config builder, watcher script generation, and Windows platform guard.

* Implemented `quest` command group with `start`, `run-step`, and `list-steps` sub-commands for analysis pipeline execution.
* Added Pydantic config schema (`pipeline/config.py`) for quest YAML validation with retry, logging, and per-step configuration.
* Added pipeline runner (`pipeline/runner.py`) with configurable retry/backoff logic, Python logging setup, and integration with the existing sequential orchestrator for checkpoint/resume support.
* Added placeholder pipeline step modules: `crawler`, `neo4j_upload`, `metadata_extractor`, and `tentris_upload` under `src/open_pulse/pipeline/`.
* Added `config/quest.example.yml` with documented example quest configuration.
* Added quest pipeline tests covering config loading, task building, disabled-step filtering, retry behaviour, checkpoint resume, CLI commands, and unknown-step error handling.

* Implemented `deploy up` command with Docker availability check, interactive profile selection via `questionary`, `.env` loading/generation from `infra/env/.env.example`, and `docker compose up -d` invocation with profile flags.
* Added `deploy down` command to tear down services with optional `--volumes` flag.
* Added `deploy ps` command to show container status.
* Added deploy command tests covering Docker-not-available error, profile flag pass-through, `.env` template creation, `down`, and `ps` sub-commands.
* Added CLI Command Reference section to `AGENTS.md` with `deploy` sub-command docs.

* Added core CLI dependencies to `pyproject.toml`: `typer`, `questionary`, `pydantic`, `pyyaml`, `python-dotenv`, and `rich`.
* Added `grimoire-ui` optional dependency group with `streamlit` for the GrimoireLab config UI.
* Replaced argparse CLI with a Typer-based entry point (`cli.py`) exposing four command groups: `deploy`, `quest`, `grimoire`, and `health` (all stubs).
* Added `src/open_pulse/commands/` package with stub modules `deploy.py`, `quest.py`, `grimoire.py`, and `health.py`.
* Added Typer CliRunner tests for every stub command; kept pure orchestrator tests unchanged.

### Changed

* Adopted standard Python src-layout: moved `pyproject.toml` and `uv.lock` from `src/` to project root so `uv` commands run from the root directory.
* Moved Dockerfile from `src/docker/Dockerfile` to `tools/images/Dockerfile-open-pulse`, matching the existing `tools/images/` convention.
* Moved tests from `src/tests/` to root-level `tests/`.
* Moved `src/scripts/run-sequential.sh` to `tools/scripts/run-sequential.sh`.
* Removed `src/README.md` (root README is sufficient).
* Updated all CI workflows, `.pre-commit-config.yaml`, `.devcontainer/devcontainer.json`, `AGENTS.md`, and root `README.md` to reference new paths.

* Moved service deployment configs (`neo4j/`, `tentris-server/`, `portainer/`) from `src/` to `infra/services/` so `src/` is reserved for CLI source code.
* Moved analysis package from `analysis/src/openpulse_analysis/` into `src/open_pulse/`, renaming the package from `openpulse-analysis` to `open-pulse`.
* Moved analysis tests from `analysis/tests/` to `src/tests/`.
* Moved analysis Dockerfile from `analysis/docker/` to `src/docker/`.
* Moved `analysis/pyproject.toml`, `analysis/uv.lock`, and `analysis/README.md` into `src/`.
* Moved `analysis/scripts/run-sequential.sh` to `src/scripts/run-sequential.sh`.
* Updated all CI workflows, `.pre-commit-config.yaml`, `.devcontainer/devcontainer.json`, `.github/CODEOWNERS`, `.gitignore`, and root `README.md` to reference new paths.
* Renamed CLI entry point from `openpulse-analysis` to `open-pulse`.

### Added

* Added `AGENTS.md` documenting the new directory layout, key commands, and conventions.

### Removed

* Removed legacy grimoire command/module layout: deleted `src/open_pulse/commands/grimoire.py` and migrated/deleted modules from `src/open_pulse/grimoire/` (`sparql_config.py`, `cronjob.py`, `streamlit_app.py`).
* Removed legacy profile-specific compose override files under `infra/compose/` (`docker-compose.analysis.override.yml`, `docker-compose.grimoirelab.override.yml`, `docker-compose.orchestration.override.yml`) in favor of the new two-file compose model.
* Removed quest step-level endpoint settings (`quest.steps.neo4j_upload.endpoint` and `quest.steps.tentris_upload.endpoint`) from pipeline config models and examples; `quest.services.*.endpoint` is now required as the canonical service configuration location.

* Removed `analysis/` directory (contents migrated to `src/`).

### Added

* Added `.github/workflows/docs-build.yml` to run Docusaurus validation with `pnpm install --frozen-lockfile` and `pnpm build` so broken links fail CI.
* Added PR docs preview artifact publishing in `docs-build` so pull requests provide downloadable static docs output.
* Added `.github/workflows/docs-pages-deploy.yml` to build validated docs artifacts from the `docs` branch and deploy them to GitHub Pages.
* Added `.github/workflows/release.yml` to trigger on stable semver tags (`vX.Y.Z`), build release assets (image archives, checksums, analysis wheel), and create draft GitHub releases with generated notes.
* Added `docs-site/docs/operations/release-checklist.md` covering `main` branch protection baseline, release execution steps, and release finalization checks.
* Added root `.pre-commit-config.yaml` with hooks for trailing whitespace, EOF fixes, YAML/JSON validation, Ruff lint/format on `analysis/`, and optional Markdown linting.
* Added `.github/workflows/ci.yml` baseline CI with path-scoped triggers and split jobs for analysis lint/tests, YAML/Markdown validation, and shell script sanity checks.
* Added `LICENSE` with Apache-2.0 terms.
* Added `CONTRIBUTING.md` with branching, PR, semantic commit, and review rules.
* Added `SECURITY.md` with private vulnerability reporting and disclosure workflow.
* Added `.github/CODEOWNERS` ownership boundaries for `src/`, `analysis/`, `infra/`, and docs paths.
* Added `.editorconfig` and `.gitattributes` for baseline repository consistency.
* Added `docs-site/` with a Docusaurus scaffold and `pnpm` scripts for docs development/build.
* Added documentation information architecture in `docs-site/docs/` with `getting-started`, `architecture`, `services`, `analysis`, and `operations` sections.
* Added explicit docs branch responsibilities in `docs-site/docs/operations/branch-model.md` (`docs` as source of truth, `main` as reference/output consumer).
* Added migration mapping from static `docs/` landing to Docusaurus source in `docs-site/docs/operations/migration-from-static-docs.md`.
* Added a root README service catalog with ports, compose profile, and status columns.
* Added a root README decision note defining `src/`, `analysis/`, and `infra/` boundaries.
* Added `infra/env/.env.example` documenting required root Compose environment variables.
* Added `infra/compose/` profile override assets for analysis, grimoirelab, and orchestration stacks.
* Added `analysis/` as an installable Python package scaffold managed by `uv`, including `pyproject.toml`, `README.md`, `src/openpulse_analysis/`, and `tests/`.
* Added `openpulse-analysis` console entry point and baseline `dev`/`test` dependency groups.
* Added `analysis/uv.lock` and initial CLI/test scaffolding to support package install, smoke runs, and packaging validation.
* Added sequential orchestration modules in `analysis/src/openpulse_analysis/` for task contracts, registry ordering, checkpoint persistence, and failure propagation.
* Added `run`, `list-tasks`, and `doctor` CLI commands with checkpoint/resume behavior and explicit non-zero failure exits.
* Added `analysis/scripts/run-sequential.sh` wrapper to invoke the sequential runner and preserve process exit codes.
* Added orchestration-focused tests in `analysis/tests/test_cli.py` for task order, failure behavior, resume flow, CLI command contracts, and wrapper semantics.
* Added `analysis/docker/Dockerfile` with a slim Python base, pinned `uv` version, non-root runtime user, and `openpulse-analysis` CLI entrypoint.
* Added `.devcontainer/` configuration for analysis development with Python 3.11, `uv` bootstrapping, and recommended VS Code extensions/settings.
* Added `.github/workflows/docker-validate.yml` with Docker Compose config validation, CI image builds for analysis/devcontainer, and Trivy-based critical vulnerability gating with scan artifacts.

### Changed

* Updated `docs-site/docs/operations/index.md` to include the release checklist in operations navigation.
* Updated `CONTRIBUTING.md` with `main` branch protection requirements, required merge checks (`ci`, `docker-validate`, `docs-build`), and semver-tag release strategy guidance.
* Updated `CONTRIBUTING.md` with pre-commit installation and all-files execution guidance for local quality checks before PRs.
* Updated `.github/workflows/ci.yml` to execute `pre-commit run --all-files` in CI for local/CI quality-gate parity.
* Normalized `.gitignore` for Python artifacts, Docker/runtime data, local data, and secret-like files.
* Updated `docs/README.md` to mark static landing as legacy and point to new docs migration/branch-model documentation.
* Updated `.gitignore` with docs tooling artifacts (`node_modules/`, `docs-site/build/`).
* Rewrote root `README.md` for onboarding with project purpose, architecture overview, DB stack quick start, `uv`-based analysis quick start, documentation navigation links, and release/contribution references.
* Refactored root `docker-compose.yml` into a profile-aware topology with default Neo4j plus opt-in `analysis`, `grimoirelab`, and `orchestration` services.
* Added healthchecks and dependency readiness gates for key profile services (`neo4j` and `grimoirelab-db`).
* Expanded `analysis/README.md` with sequential orchestration usage and checkpoint resume guidance.
* Expanded `analysis/README.md` with container build/smoke/non-root checks and devcontainer setup guidance.
