"""Paginated read-only browser for the per-collection DuckDB source-of-truth tables.

Each Qdrant collection on the hub is backed by a DuckDB table that holds
the raw rows the embeddings + Qdrant points were derived from. This
module exposes a small read-only adapter the ``/api/hub/c/<name>/rows``
route can call to render a table view on the collection landing page.

Only collections that appear in :data:`_BACKING` are addressable —
unknown names return ``None``. Table names are hardcoded too; the
``name`` query parameter is never interpolated into SQL.

The DuckDB connection is opened ``read_only=True`` per request (cheap
on DuckDB) so an underlying file replacement is picked up without a
hub restart. Row counts are cached per-table because they don't change
between writes and a ``SELECT COUNT(*)`` over 100k rows can take 100s
of ms otherwise.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stat:
    """One headline stat for the collection landing page.

    The SQL must return a single scalar (one row, one column). The
    ``unit`` hint tells the UI how to format the value: ``"int"`` →
    thousand-separator, ``"pct"`` → ``XX%``, ``"str"`` → as-is.
    """

    label: str
    sql: str
    unit: str = "int"


@dataclass(frozen=True)
class Backing:
    """How a Qdrant collection maps to a DuckDB table."""

    db_path: Path
    table: str
    # Columns we deliberately hide from the table view because they're
    # huge / binary / not useful to scroll past (vectors, raw blobs,
    # embedding_text duplicates the human-readable fields).
    hidden_cols: tuple[str, ...] = ()
    # Four small headline stats rendered at the top of the collection
    # page. Empty tuple → no stats card.
    stats: tuple[Stat, ...] = ()
    # Columns the search bar greps over (case-insensitive substring).
    # Empty tuple → no search box.
    search_cols: tuple[str, ...] = ()
    # Example terms shown as clickable chips below the search input.
    search_examples: tuple[str, ...] = ()
    # Optional full SELECT statement used as the source for the row
    # browser instead of the bare ``"<table>"``. Lets us expose
    # joined / derived columns (e.g. records + aggregated community
    # list) without writing to the DuckDB. When ``None`` the browser
    # behaves as before — straight reads off ``table``.
    select_sql: str | None = None


# Only the surfaces the user asked for in this pass — OAM Monitor +
# GitHub repos. Other DuckDBs (openalex, infoscience, ror, ...) ship in
# the same shape and can be added here one line at a time.
_DATA_ROOT = Path(os.environ.get("HUB_DATA_DIR_HOST", "/data"))

_BACKING: dict[str, Backing] = {
    "github_repos": Backing(
        db_path=_DATA_ROOT / "index/github_repos/duckdb/github_repos.duckdb",
        table="repos",
        hidden_cols=(),
        stats=(
            Stat("Total repositories", "SELECT COUNT(*) FROM repos"),
            Stat("Distinct owners", "SELECT COUNT(DISTINCT owner) FROM repos"),
            Stat(
                "Total stars",
                "SELECT COALESCE(SUM(stargazers_count), 0) FROM repos",
            ),
            Stat(
                "Top language",
                "SELECT primary_language FROM repos "
                "WHERE primary_language IS NOT NULL AND primary_language <> '' "
                "GROUP BY primary_language ORDER BY COUNT(*) DESC LIMIT 1",
                unit="str",
            ),
        ),
        search_cols=("repo_id", "owner", "name", "description"),
        search_examples=("epfl", "deep learning", "snakemake", "rust"),
    ),
    "oamonitor_publications": Backing(
        db_path=_DATA_ROOT / "index/oamonitor/duckdb/oamonitor.duckdb",
        table="publications",
        hidden_cols=("embedding_text", "raw"),
        stats=(
            Stat("Total publications", "SELECT COUNT(*) FROM publications"),
            Stat(
                "Distinct publishers",
                "SELECT COUNT(DISTINCT publisher_id) FROM publications "
                "WHERE publisher_id IS NOT NULL",
            ),
            Stat(
                # oa_color is INTEGER (0 = closed, anything else = some OA flavour).
                "Open-access share",
                "SELECT 100.0 * COUNT(*) FILTER (WHERE oa_color <> 0) "
                "/ NULLIF(COUNT(*), 0) FROM publications",
                unit="pct",
            ),
            Stat(
                "Median year",
                "SELECT CAST(median(published_year) AS INTEGER) FROM publications "
                "WHERE published_year IS NOT NULL",
                unit="str",
            ),
        ),
        search_cols=("_id", "doi", "url", "publisher_name", "license"),
        search_examples=("10.1038", "nature", "springer", "elsevier"),
    ),
    "oamonitor_journals": Backing(
        db_path=_DATA_ROOT / "index/oamonitor/duckdb/oamonitor.duckdb",
        table="journals",
        hidden_cols=("embedding_text", "raw"),
        stats=(
            Stat("Total journals", "SELECT COUNT(*) FROM journals"),
            Stat(
                "Open-access journals",
                "SELECT COUNT(*) FROM journals WHERE oa_color <> 0",
            ),
            Stat(
                "Closed-access journals",
                "SELECT COUNT(*) FROM journals WHERE oa_color = 0",
            ),
            Stat(
                "With ISSN",
                "SELECT COUNT(*) FROM journals WHERE issns IS NOT NULL",
            ),
        ),
        search_cols=("title", "issns"),
        search_examples=("nature", "epfl", "science", "ieee"),
    ),
    "oamonitor_organisations": Backing(
        db_path=_DATA_ROOT / "index/oamonitor/duckdb/oamonitor.duckdb",
        table="organisations",
        hidden_cols=("embedding_text", "raw"),
        stats=(
            Stat("Total organisations", "SELECT COUNT(*) FROM organisations"),
            Stat(
                "Distinct countries",
                "SELECT COUNT(DISTINCT country_code) FROM organisations "
                "WHERE country_code IS NOT NULL",
            ),
            Stat(
                "Distinct types",
                "SELECT COUNT(DISTINCT type) FROM organisations WHERE type IS NOT NULL",
            ),
            Stat(
                "With GRID id",
                "SELECT COUNT(*) FROM organisations WHERE grid_id IS NOT NULL",
            ),
        ),
        search_cols=("name", "type", "country_code", "acronyms"),
        search_examples=("EPFL", "Swiss", "University", "CH"),
    ),
    "zenodo_records": Backing(
        db_path=_DATA_ROOT / "index/zenodo/duckdb/zenodo.duckdb",
        table="records",
        hidden_cols=("raw", "keywords_json"),
        # SELECT joins ``record_communities`` so each record carries an
        # aggregated list of communities it belongs to. LEFT JOIN keeps
        # records with no community — their ``community`` cell renders
        # as ``[]``. ``LIST(DISTINCT ...)`` dedupes when the join table
        # repeats a pair.
        #
        # 2.1.0rc1 surface: ``concept_doi`` is the "all versions" DOI
        # (long-term citation); ``version`` / ``revision`` show how the
        # record evolved; ``created_at`` / ``updated_at`` are lifecycle
        # timestamps; ``views`` / ``downloads`` and their ``unique_*`` /
        # ``version_*`` siblings are reach metrics from Zenodo stats.
        select_sql=(
            "SELECT r.zenodo_id, r.doi, r.concept_doi, r.title, "
            "r.description, r.publication_date, r.resource_type, "
            "r.access_right, r.license_id, r.version, r.revision, "
            "r.created_at, r.updated_at, "
            "r.views, r.unique_views, r.downloads, r.unique_downloads, "
            "r.version_views, r.version_unique_views, "
            "r.version_downloads, r.version_unique_downloads, "
            "r.keywords_json, r.raw, r.ingested_at, r.concept_recid, "
            "LIST(DISTINCT rc.community_id) FILTER "
            "(WHERE rc.community_id IS NOT NULL) AS community "
            "FROM records r "
            "LEFT JOIN record_communities rc ON r.zenodo_id = rc.record_id "
            "GROUP BY r.zenodo_id, r.doi, r.concept_doi, r.title, "
            "r.description, r.publication_date, r.resource_type, "
            "r.access_right, r.license_id, r.version, r.revision, "
            "r.created_at, r.updated_at, "
            "r.views, r.unique_views, r.downloads, r.unique_downloads, "
            "r.version_views, r.version_unique_views, "
            "r.version_downloads, r.version_unique_downloads, "
            "r.keywords_json, r.raw, r.ingested_at, r.concept_recid"
        ),
        stats=(
            Stat("Total records", "SELECT COUNT(*) FROM records"),
            Stat(
                "Distinct concept DOIs",
                "SELECT COUNT(DISTINCT concept_doi) FROM records "
                "WHERE concept_doi IS NOT NULL",
            ),
            Stat(
                "Total downloads",
                "SELECT COALESCE(SUM(downloads), 0) FROM records",
            ),
            Stat(
                "Median views",
                "SELECT CAST(median(views) AS BIGINT) FROM records "
                "WHERE views IS NOT NULL",
            ),
        ),
        # Include ``community`` in search_cols so typing ``epfl`` filters
        # to records affiliated with that community. ILIKE on a list
        # column casts to text and matches the stringified array, which
        # is exactly the substring the user expects.
        search_cols=("zenodo_id", "doi", "concept_doi", "title",
                     "description", "version", "community"),
        search_examples=("dataset", "epfl", "10.5281", "machine learning"),
    ),
    # New in GME 2.1.0rc1 — the cross-platform communities registry.
    # Today every row is ``source=zenodo``; the schema is designed for
    # future GitHub orgs / OpenAlex institutions / ROR-anchored groups,
    # so the table view exposes ``source`` + ``source_slug`` up front so
    # operators can see the cardinality split as new sources come in.
    "communities": Backing(
        db_path=_DATA_ROOT / "index/communities/duckdb/communities.duckdb",
        table="communities",
        hidden_cols=("raw", "curator_names", "keywords"),
        stats=(
            Stat("Total communities", "SELECT COUNT(*) FROM communities"),
            Stat(
                "Distinct sources",
                "SELECT COUNT(DISTINCT source) FROM communities",
            ),
            Stat(
                "Distinct parent orgs",
                "SELECT COUNT(DISTINCT parent_org) FROM communities "
                "WHERE parent_org IS NOT NULL",
            ),
            Stat(
                "Public",
                "SELECT COUNT(*) FROM communities WHERE visibility = 'public'",
            ),
        ),
        search_cols=(
            "community_id", "source_slug", "parent_org", "title", "description",
        ),
        search_examples=("epfl", "cern", "ethz", "openlab"),
    ),
    "oamonitor_publishers": Backing(
        db_path=_DATA_ROOT / "index/oamonitor/duckdb/oamonitor.duckdb",
        table="publishers",
        hidden_cols=("embedding_text", "raw"),
        stats=(
            Stat("Total publishers", "SELECT COUNT(*) FROM publishers"),
            Stat(
                "Open-access publishers",
                "SELECT COUNT(*) FROM publishers WHERE oa_color <> 0",
            ),
            Stat(
                "Closed publishers",
                "SELECT COUNT(*) FROM publishers WHERE oa_color = 0",
            ),
            Stat(
                "With embedding",
                "SELECT COUNT(*) FROM publishers WHERE embedded_at IS NOT NULL",
            ),
        ),
        search_cols=("name",),
        search_examples=("springer", "elsevier", "nature", "wiley"),
    ),
}


# Remaining hub collections — wired as ``(db_path, table)`` pairs and
# enriched on module import with auto-derived stats + search columns
# (see ``_build_auto_backing``). ROR / SNSF have one DuckDB shared
# across regional Qdrant collections; we point each to the full table
# and let the search bar narrow it.
_AUTO_TABLES: dict[str, tuple[Path, str]] = {
    # GitHub split stores — people + orgs of the crawled ecosystem.
    # (``github_repos`` has a hand-tuned Backing above.)
    "github_users": (
        _DATA_ROOT / "index/github_users/duckdb/github_users.duckdb",
        "users",
    ),
    "github_organizations": (
        _DATA_ROOT / "index/github_organizations/duckdb/github_organizations.duckdb",
        "organizations",
    ),
    # Split HuggingFace stores (GME 3.0.0rc1 layout) — browsable row tables.
    "huggingface_models": (
        _DATA_ROOT / "index/huggingface_models/duckdb/huggingface_models.duckdb",
        "models",
    ),
    "huggingface_datasets": (
        _DATA_ROOT / "index/huggingface_datasets/duckdb/huggingface_datasets.duckdb",
        "datasets",
    ),
    "huggingface_organizations": (
        _DATA_ROOT / "index/huggingface_organizations/duckdb/huggingface_organizations.duckdb",
        "organizations",
    ),
    "huggingface_spaces": (
        _DATA_ROOT / "index/huggingface_spaces/duckdb/huggingface_spaces.duckdb",
        "spaces",
    ),
    # OpenAlex — shared by the generic "authors" / "concepts" / ...
    "authors": (
        _DATA_ROOT / "index/openalex/duckdb/openalex.duckdb",
        "authors",
    ),
    "concepts": (
        _DATA_ROOT / "index/openalex/duckdb/openalex.duckdb",
        "concepts",
    ),
    "institutions": (
        _DATA_ROOT / "index/openalex/duckdb/openalex.duckdb",
        "institutions",
    ),
    "sources": (
        _DATA_ROOT / "index/openalex/duckdb/openalex.duckdb",
        "sources",
    ),
    "topics": (
        _DATA_ROOT / "index/openalex/duckdb/openalex.duckdb",
        "topics",
    ),
    "works": (
        _DATA_ROOT / "index/openalex/duckdb/openalex.duckdb",
        "works",
    ),
    # OpenAlex relational + GitHub-extracted tables. ``work_github_urls``
    # is the highest-signal addition for cross-corpus joins — every row
    # is a (research paper, GitHub repo) edge our pipeline can use to
    # anchor a Person's contributions to their published work.
    "openalex_work_authors": (
        _DATA_ROOT / "index/openalex/duckdb/openalex.duckdb",
        "work_authors",
    ),
    "openalex_work_institutions": (
        _DATA_ROOT / "index/openalex/duckdb/openalex.duckdb",
        "work_institutions",
    ),
    "openalex_work_references": (
        _DATA_ROOT / "index/openalex/duckdb/openalex.duckdb",
        "work_references",
    ),
    "openalex_work_github_urls": (
        _DATA_ROOT / "index/openalex/duckdb/openalex.duckdb",
        "work_github_urls",
    ),
    # EPFL Graph
    "epfl_graph_disciplines": (
        _DATA_ROOT / "index/epfl_graph/duckdb/epfl_graph.duckdb",
        "categories",
    ),
    # 39k discipline → concept edges. Browses the same EPFL discipline
    # taxonomy as ``epfl_graph_disciplines`` but at the concept level
    # (Wikipedia-grounded sub-topics), so the row count matches the
    # number of (category, concept) pairs.
    "epfl_graph_concepts": (
        _DATA_ROOT / "index/epfl_graph/duckdb/epfl_graph.duckdb",
        "category_concepts",
    ),
    # ETH-Z Research Collection
    "ethz_research_collection_articles": (
        _DATA_ROOT
        / "index/ethz-research-collection/duckdb/ethz_research_collection.duckdb",
        "articles",
    ),
    "ethz_research_collection_organizations": (
        _DATA_ROOT
        / "index/ethz-research-collection/duckdb/ethz_research_collection.duckdb",
        "organizations",
    ),
    "ethz_research_collection_persons": (
        _DATA_ROOT
        / "index/ethz-research-collection/duckdb/ethz_research_collection.duckdb",
        "persons",
    ),
    # ETH-Z Research Collection relational joins — let the row browser
    # pivot from article ↔ author ↔ org ↔ host without leaving the
    # collection. Same shape as the infoscience tables below.
    "ethz_research_collection_article_persons": (
        _DATA_ROOT
        / "index/ethz-research-collection/duckdb/ethz_research_collection.duckdb",
        "article_persons",
    ),
    "ethz_research_collection_article_orgs": (
        _DATA_ROOT
        / "index/ethz-research-collection/duckdb/ethz_research_collection.duckdb",
        "article_orgs",
    ),
    "ethz_research_collection_article_links": (
        _DATA_ROOT
        / "index/ethz-research-collection/duckdb/ethz_research_collection.duckdb",
        "article_links",
    ),
    # Hugging Face
    "hf_datasets": (
        _DATA_ROOT / "index/huggingface/duckdb/huggingface.duckdb",
        "datasets",
    ),
    "hf_models": (
        _DATA_ROOT / "index/huggingface/duckdb/huggingface.duckdb",
        "models",
    ),
    "hf_orgs": (
        _DATA_ROOT / "index/huggingface/duckdb/huggingface.duckdb",
        "orgs",
    ),
    "hf_spaces": (
        _DATA_ROOT / "index/huggingface/duckdb/huggingface.duckdb",
        "spaces",
    ),
    # Infoscience
    "infoscience_articles": (
        _DATA_ROOT / "index/infoscience/duckdb/infoscience.duckdb",
        "articles",
    ),
    "infoscience_organizations": (
        _DATA_ROOT / "index/infoscience/duckdb/infoscience.duckdb",
        "organizations",
    ),
    "infoscience_persons": (
        _DATA_ROOT / "index/infoscience/duckdb/infoscience.duckdb",
        "persons",
    ),
    # Infoscience relational joins — article ↔ author / org / external
    # host. ``article_links`` carries hostnames + URLs of every reference
    # the article points at (preprint mirror, dataset DOI, GitHub repo),
    # which is the table the Hub's `connected/<ref>` traversal queries
    # when surfacing a paper's full footprint.
    "infoscience_article_persons": (
        _DATA_ROOT / "index/infoscience/duckdb/infoscience.duckdb",
        "article_persons",
    ),
    "infoscience_article_orgs": (
        _DATA_ROOT / "index/infoscience/duckdb/infoscience.duckdb",
        "article_orgs",
    ),
    "infoscience_article_links": (
        _DATA_ROOT / "index/infoscience/duckdb/infoscience.duckdb",
        "article_links",
    ),
    # ORCID
    "orcid_epfl_educations": (
        _DATA_ROOT / "index/orcid-epfl/duckdb/orcid.duckdb",
        "educations",
    ),
    "orcid_epfl_employments": (
        _DATA_ROOT / "index/orcid-epfl/duckdb/orcid.duckdb",
        "employments",
    ),
    "orcid_epfl_persons": (
        _DATA_ROOT / "index/orcid-epfl/duckdb/orcid.duckdb",
        "persons",
    ),
    "orcid_switzerland_employments": (
        _DATA_ROOT / "index/orcid-switzerland/duckdb/orcid.duckdb",
        "employments",
    ),
    "orcid_switzerland_persons": (
        _DATA_ROOT / "index/orcid-switzerland/duckdb/orcid.duckdb",
        "persons",
    ),
    # RenkuLab
    "renkulab_data_connectors": (
        _DATA_ROOT / "index/renkulab/duckdb/renkulab.duckdb",
        "data_connectors",
    ),
    "renkulab_groups": (
        _DATA_ROOT / "index/renkulab/duckdb/renkulab.duckdb",
        "groups",
    ),
    "renkulab_projects": (
        _DATA_ROOT / "index/renkulab/duckdb/renkulab.duckdb",
        "projects",
    ),
    "renkulab_users": (
        _DATA_ROOT / "index/renkulab/duckdb/renkulab.duckdb",
        "users",
    ),
    # Renkulab membership joins — who belongs to which group / project.
    # Surfaced directly (like the other N:M join tables) so the row
    # browser can answer "members of group X" / "members of project Y".
    "renkulab_group_members": (
        _DATA_ROOT / "index/renkulab/duckdb/renkulab.duckdb",
        "group_members",
    ),
    "renkulab_project_members": (
        _DATA_ROOT / "index/renkulab/duckdb/renkulab.duckdb",
        "project_members",
    ),
    # ROR — same ``records`` table for every regional flavour; the
    # per-tile WHERE clause that turns ``ror_switzerland`` into the
    # 1.8k-row Swiss subset lives in ``_AUTO_FILTERS`` below.
    "ror_epfl_ethz": (_DATA_ROOT / "index/ror/duckdb/ror.duckdb", "records"),
    "ror_europe": (_DATA_ROOT / "index/ror/duckdb/ror.duckdb", "records"),
    "ror_switzerland": (
        _DATA_ROOT / "index/ror/duckdb/ror.duckdb",
        "records",
    ),
    "ror_worldwide": (_DATA_ROOT / "index/ror/duckdb/ror.duckdb", "records"),
    # SNSF — same here. ``grants`` is the headline table; the EPFL and
    # ETHZ flavours are filtered via ``_AUTO_FILTERS`` so the home tile
    # counts and the row browser show the right per-institution subset.
    "snsf_epfl": (_DATA_ROOT / "index/snsf/duckdb/snsf.duckdb", "grants"),
    "snsf_ethz": (_DATA_ROOT / "index/snsf/duckdb/snsf.duckdb", "grants"),
    "snsf_switzerland": (
        _DATA_ROOT / "index/snsf/duckdb/snsf.duckdb",
        "grants",
    ),
    # SNSF — research outputs broken out per type. Every row is keyed
    # by ``grant_number`` so the row browser can pivot from any output
    # back to the funding grant that produced it. ``persons`` is the
    # 146k researcher registry every grant + output references.
    "snsf_persons": (_DATA_ROOT / "index/snsf/duckdb/snsf.duckdb", "persons"),
    "snsf_output_publications": (
        _DATA_ROOT / "index/snsf/duckdb/snsf.duckdb",
        "output_publications",
    ),
    "snsf_output_datasets": (
        _DATA_ROOT / "index/snsf/duckdb/snsf.duckdb",
        "output_datasets",
    ),
    "snsf_output_academic_events": (
        _DATA_ROOT / "index/snsf/duckdb/snsf.duckdb",
        "output_academic_events",
    ),
    "snsf_output_knowledge_transfers": (
        _DATA_ROOT / "index/snsf/duckdb/snsf.duckdb",
        "output_knowledge_transfers",
    ),
    "snsf_output_public_communications": (
        _DATA_ROOT / "index/snsf/duckdb/snsf.duckdb",
        "output_public_communications",
    ),
    "snsf_output_collaborations": (
        _DATA_ROOT / "index/snsf/duckdb/snsf.duckdb",
        "output_collaborations",
    ),
    "snsf_output_use_inspired": (
        _DATA_ROOT / "index/snsf/duckdb/snsf.duckdb",
        "output_use_inspired",
    ),
    # SwissUBase — studies is the largest non-empty table.
    "swissubase_entities": (
        _DATA_ROOT / "index/swissubase/duckdb/swissubase.duckdb",
        "studies",
    ),
    # SwissUBase author + institution registries. Cross-walked into the
    # studies via ``study_persons`` / ``study_institutions`` join
    # tables — kept here for direct lookup ("which Swiss social-sciences
    # ORCIDs appear in our corpus?", "which institutions submit studies
    # via SwissUBase?").
    "swissubase_persons": (
        _DATA_ROOT / "index/swissubase/duckdb/swissubase.duckdb",
        "persons",
    ),
    "swissubase_institutions": (
        _DATA_ROOT / "index/swissubase/duckdb/swissubase.duckdb",
        "institutions",
    ),
    # SwissUBase ``datasets`` is the per-study data-file inventory (the
    # studies are the headline records; datasets are their deposited
    # resources). ``study_persons`` / ``study_institutions`` are the N:M
    # joins the comment above refers to — surfaced directly so the row
    # browser can answer "which persons/institutions back study X".
    "swissubase_datasets": (
        _DATA_ROOT / "index/swissubase/duckdb/swissubase.duckdb",
        "datasets",
    ),
    "swissubase_study_persons": (
        _DATA_ROOT / "index/swissubase/duckdb/swissubase.duckdb",
        "study_persons",
    ),
    "swissubase_study_institutions": (
        _DATA_ROOT / "index/swissubase/duckdb/swissubase.duckdb",
        "study_institutions",
    ),
    # Zenodo — ``zenodo_records`` is hand-tuned in ``_BACKING`` above
    # (it joins ``record_communities`` to expose a per-record community
    # list). ``communities`` + ``creators`` are exposed here so the
    # row browser can serve them directly via ``/hub/c/<name>`` even
    # though they don't have their own Qdrant collection (yet — they
    # live only in the DuckDB source-of-truth).
    "zenodo_communities": (
        _DATA_ROOT / "index/zenodo/duckdb/zenodo.duckdb",
        "communities",
    ),
    "zenodo_creators": (
        _DATA_ROOT / "index/zenodo/duckdb/zenodo.duckdb",
        "creators",
    ),
    # Zenodo relational + file inventory. ``files`` is the per-record
    # asset list (checksums, sizes, download URLs); ``record_creators``
    # and ``record_communities`` are the N:M joins powering the
    # author / community lists already aggregated into the headline
    # ``zenodo_records`` view. Exposed separately so consumers can hit
    # them directly for cross-record analytics (e.g. "every record by
    # creator X", "every record in community Y").
    "zenodo_files": (
        _DATA_ROOT / "index/zenodo/duckdb/zenodo.duckdb",
        "files",
    ),
    "zenodo_record_creators": (
        _DATA_ROOT / "index/zenodo/duckdb/zenodo.duckdb",
        "record_creators",
    ),
    "zenodo_record_communities": (
        _DATA_ROOT / "index/zenodo/duckdb/zenodo.duckdb",
        "record_communities",
    ),
    # DockerHub — container images.
    "dockerhub": (
        _DATA_ROOT / "index/dockerhub/duckdb/dockerhub.duckdb",
        "images",
    ),
    # HuggingFace Daily Papers (arXiv-linked).
    "huggingface_papers": (
        _DATA_ROOT / "index/huggingface_papers/duckdb/huggingface_papers.duckdb",
        "papers",
    ),
    # GitLab — one split store per (instance, entity). Each lives in its
    # own DuckDB named after the collection; the headline table is the
    # entity name (groups / projects / users).
    "gitlab_epfl_groups": (
        _DATA_ROOT / "index/gitlab_epfl_groups/duckdb/gitlab_epfl_groups.duckdb",
        "groups",
    ),
    "gitlab_epfl_projects": (
        _DATA_ROOT / "index/gitlab_epfl_projects/duckdb/gitlab_epfl_projects.duckdb",
        "projects",
    ),
    "gitlab_epfl_users": (
        _DATA_ROOT / "index/gitlab_epfl_users/duckdb/gitlab_epfl_users.duckdb",
        "users",
    ),
    "gitlab_ethz_groups": (
        _DATA_ROOT / "index/gitlab_ethz_groups/duckdb/gitlab_ethz_groups.duckdb",
        "groups",
    ),
    "gitlab_ethz_projects": (
        _DATA_ROOT / "index/gitlab_ethz_projects/duckdb/gitlab_ethz_projects.duckdb",
        "projects",
    ),
    "gitlab_ethz_users": (
        _DATA_ROOT / "index/gitlab_ethz_users/duckdb/gitlab_ethz_users.duckdb",
        "users",
    ),
    "gitlab_datascience_groups": (
        _DATA_ROOT / "index/gitlab_datascience_groups/duckdb/gitlab_datascience_groups.duckdb",
        "groups",
    ),
    "gitlab_datascience_projects": (
        _DATA_ROOT / "index/gitlab_datascience_projects/duckdb/gitlab_datascience_projects.duckdb",
        "projects",
    ),
    "gitlab_datascience_users": (
        _DATA_ROOT / "index/gitlab_datascience_users/duckdb/gitlab_datascience_users.duckdb",
        "users",
    ),
}


# Example search chips per auto-backed collection. Picked to demo the
# kinds of queries that hit useful matches — names of institutions /
# models / venues / topics — not arbitrary keywords.
_AUTO_SEARCH_EXAMPLES: dict[str, tuple[str, ...]] = {
    # OpenAlex
    "authors": ("LeCun", "Hinton", "EPFL", "ETH"),
    "concepts": ("machine learning", "quantum", "biology", "neural"),
    "institutions": ("EPFL", "ETH", "MIT", "Switzerland"),
    "sources": ("Nature", "Science", "IEEE", "ACM"),
    "topics": ("artificial intelligence", "genetics", "climate", "robotics"),
    "openalex_work_authors": ("W2", "A5", "first", "corresponding"),
    "openalex_work_institutions": ("EPFL", "ETH", "MIT", "I"),
    "openalex_work_references": ("W2", "W3", "W4", "W5"),
    "openalex_work_github_urls": ("torvalds", "tensorflow", "pytorch", "epfl"),
    # EPFL Graph
    "epfl_graph_disciplines": ("physics", "computer", "biology", "mathematics"),
    "epfl_graph_concepts": ("machine", "neural", "protein", "quantum"),
    # ETH-Z Research Collection
    "ethz_research_collection_articles": (
        "deep learning",
        "robotics",
        "swiss",
        "quantum",
    ),
    "ethz_research_collection_organizations": ("Department", "Institute", "Laboratory"),
    "ethz_research_collection_persons": ("Müller", "Schmidt", "Anna", "Wolfgang"),
    "ethz_research_collection_article_persons": ("first", "corresponding", "co-author", "second"),
    "ethz_research_collection_article_orgs": ("Department", "Institute", "ETH", "Zurich"),
    "ethz_research_collection_article_links": ("doi.org", "github.com", "arxiv", "zenodo"),
    # Hugging Face
    "hf_datasets": ("imagenet", "translation", "audio", "code"),
    "hf_models": ("llama", "bert", "diffusion", "whisper"),
    "hf_orgs": ("google", "meta", "microsoft", "stability"),
    "hf_spaces": ("chat", "image", "demo", "translator"),
    # Infoscience
    "infoscience_articles": ("deep learning", "epfl", "physics", "quantum"),
    "infoscience_organizations": ("Laboratory", "Institute", "Lab", "Group"),
    "infoscience_persons": ("Patrick", "Anna", "Müller", "Martin"),
    "infoscience_article_persons": ("first", "corresponding", "co-author", "second"),
    "infoscience_article_orgs": ("Laboratory", "Institute", "EPFL", "Lausanne"),
    "infoscience_article_links": ("doi.org", "github.com", "arxiv", "zenodo"),
    # ORCID
    "orcid_epfl_educations": ("EPFL", "Lausanne", "PhD", "Master"),
    "orcid_epfl_employments": ("Professor", "PostDoc", "EPFL", "Researcher"),
    "orcid_epfl_persons": ("Anna", "Patrick", "Müller", "Maria"),
    "orcid_switzerland_employments": ("Professor", "ETH", "EPFL", "Zurich"),
    "orcid_switzerland_persons": ("Anna", "Müller", "Patrick", "Schmidt"),
    # RenkuLab
    "renkulab_data_connectors": ("github", "s3", "azure", "doi"),
    "renkulab_groups": ("research", "epfl", "course", "lab"),
    "renkulab_projects": ("machine learning", "tutorial", "data", "python"),
    "renkulab_users": ("epfl", "alice", "patrick", "anna"),
    # ROR
    "ror_epfl_ethz": ("EPFL", "ETH", "Lausanne", "Zurich"),
    "ror_europe": ("Cambridge", "Oxford", "Munich", "Paris"),
    "ror_switzerland": ("EPFL", "ETH", "Zurich", "Basel"),
    "ror_worldwide": ("Stanford", "MIT", "Harvard", "Tokyo"),
    # SNSF
    "snsf_epfl": ("EPFL", "machine learning", "physics", "Lausanne"),
    "snsf_ethz": ("ETH", "Zurich", "robotics", "quantum"),
    "snsf_switzerland": ("Swiss", "professor", "biology", "chemistry"),
    "snsf_persons": ("Patrick", "Müller", "EPFL", "ETH"),
    "snsf_output_publications": (
        "machine learning", "quantum", "nature", "epfl",
    ),
    "snsf_output_datasets": ("dataset", "zenodo", "10.5281", "swiss"),
    "snsf_output_academic_events": ("conference", "workshop", "ICML", "NeurIPS"),
    "snsf_output_knowledge_transfers": (
        "patent", "spin-off", "industry", "transfer",
    ),
    "snsf_output_public_communications": (
        "media", "talk", "press", "interview",
    ),
    "snsf_output_collaborations": (
        "Switzerland", "Germany", "United States", "industry",
    ),
    "snsf_output_use_inspired": ("application", "industry", "use", "EPFL"),
    # SwissUBase
    "swissubase_entities": ("survey", "FORS", "Switzerland", "households"),
    "swissubase_persons": ("Müller", "Schmidt", "Patrick", "Anna"),
    "swissubase_institutions": (
        "FORS", "Lausanne", "Bern", "Switzerland",
    ),
    "swissubase_datasets": ("survey", "data", "wave", "panel"),
    # Zenodo
    "zenodo_records": ("dataset", "epfl", "10.5281", "machine learning"),
    "zenodo_communities": ("epfl", "swiss", "open", "research"),
    "zenodo_creators": ("EPFL", "Müller", "Patrick", "Anna"),
    "zenodo_files": (".pdf", ".csv", ".zip", "dataset"),
    "zenodo_record_creators": ("first", "corresponding", "co-author", "1"),
    "zenodo_record_communities": ("epfl", "cern", "ethz", "openlab"),
    # DockerHub / HuggingFace papers / GitLab
    "dockerhub": ("library", "epfl", "python", "official"),
    "huggingface_papers": ("transformer", "diffusion", "llm", "agent"),
    "gitlab_epfl_groups": ("lab", "epfl", "research", "course"),
    "gitlab_epfl_projects": ("python", "thesis", "data", "epfl"),
    "gitlab_epfl_users": ("anna", "patrick", "müller", "martin"),
    "gitlab_ethz_groups": ("eth", "institute", "lab", "course"),
    "gitlab_ethz_projects": ("robotics", "thesis", "data", "eth"),
    "gitlab_ethz_users": ("müller", "schmidt", "anna", "thomas"),
    "gitlab_datascience_groups": ("sdsc", "renku", "research", "course"),
    "gitlab_datascience_projects": ("renku", "machine learning", "data", "tutorial"),
    "gitlab_datascience_users": ("sdsc", "alice", "patrick", "anna"),
}


# Heuristic stat templates, applied in order. Each entry yields one
# headline tile when the table has a column that matches.
# (label, candidate_col_names, sql_template{c,t}, unit)
_AUTO_STAT_PATTERNS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    (
        "Distinct countries",
        ("country", "country_code", "country_iso"),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
    (
        "Distinct types",
        ("type", "kind", "category", "model_type", "library_name"),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
    (
        "Distinct languages",
        ("language", "primary_language", "lang"),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
    (
        "Distinct hosts",
        ("host", "publisher_name", "publisher", "venue"),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
    (
        "Distinct owners",
        ("owner", "organisation", "organization", "affiliation"),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
    (
        "Total downloads",
        ("downloads", "downloads_count", "download_count"),
        'SELECT COALESCE(SUM(CAST("{c}" AS BIGINT)), 0) FROM "{t}"',
        "int",
    ),
    (
        "Total likes",
        ("likes", "likes_count", "stargazers_count"),
        'SELECT COALESCE(SUM(CAST("{c}" AS BIGINT)), 0) FROM "{t}"',
        "int",
    ),
    (
        "Median year",
        ("year", "published_year", "publication_year", "publicationYear"),
        'SELECT CAST(median("{c}") AS INTEGER) FROM "{t}" WHERE "{c}" IS NOT NULL',
        "str",
    ),
    (
        "Distinct ORCIDs",
        ("orcid", "orcid_id"),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
    (
        "Distinct DOIs",
        ("doi",),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
)

# Text-like column names worth grep'ing over by default.
_AUTO_SEARCH_PRIORITY = (
    "name",
    "title",
    "label",
    "display_name",
    "description",
    "owner",
    "author",
    "creator",
    "publisher_name",
    "publisher",
    "id",
    "_id",
    "doi",
    "url",
    "homepage",
)


# Columns hidden from the table view — bulky blobs and embedding vectors.
def _is_hidden_col(col: str) -> bool:
    nl = col.lower()
    return (
        "embedding" in nl
        or "_vector" in nl
        or "_blob" in nl
        or nl == "raw"
        or nl == "content"
    )


# Per-collection WHERE filter applied when several collections share
# the same underlying DuckDB table — without these, every regional
# flavour reports the worldwide row count and the row browser shows
# the same unfiltered set behind every tile. The WHERE is wrapped
# into the ``Backing.select_sql`` so it propagates uniformly to
# ``row_count_for``, ``/api/hub/c/<name>/rows``, ``/export``, and the
# stats panel's Total counter (the secondary auto-stats still aggregate
# over the bare table — a known second-order limitation).
_AUTO_FILTERS: dict[str, str] = {
    # ROR — country-scoped subsets of ``records``. ``ror_worldwide``
    # has no filter (everything counts). Europe uses the EU + EFTA +
    # UK + non-EU European geography (Liechtenstein, Norway, Iceland,
    # the Western Balkans, Türkiye, etc.) so a Swiss-region query
    # still finds CH partners in the obvious "Europe" view.
    "ror_europe": (
        "country_code IN ('AT','BE','BG','HR','CY','CZ','DK','EE','FI',"
        "'FR','DE','GR','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT',"
        "'RO','SK','SI','ES','SE','CH','NO','IS','GB','LI','MC','SM','AD',"
        "'VA','UA','RS','MK','ME','BA','AL','MD','BY','RU','TR')"
    ),
    "ror_switzerland": "country_code = 'CH'",
    # The EPFL/ETHZ tile is a shortlist — institutions whose ROR
    # display name carries the EPFL or ETH-Zürich brand. Catches both
    # the parent record and the small set of affiliated centers that
    # ROR indexes under those acronyms.
    "ror_epfl_ethz": (
        "name ILIKE '%EPFL%' "
        "OR name ILIKE '%Ecole polytechnique fédérale de Lausanne%' "
        "OR (name ILIKE '%ETH%' "
        "    AND (name ILIKE '%Zürich%' OR name ILIKE '%Zurich%'))"
    ),
    # SNSF — institution-scoped subsets of ``grants``. The Swiss flavour
    # is the unfiltered table (every SNSF grant is by definition Swiss),
    # EPFL and ETHZ are matched against the canonical ``institute``
    # column where the values look like ``"Laboratory of … EPFL - …"``
    # or ``"Institut für … ETH Zürich"``.
    "snsf_epfl": (
        "institute ILIKE '%EPFL%' "
        "OR institute ILIKE '%polytechnique%lausanne%'"
    ),
    "snsf_ethz": (
        "institute ILIKE '%ETH%Zürich%' "
        "OR institute ILIKE '%ETH Zurich%' "
        "OR institute ILIKE '%ETHZ%'"
    ),
}


# Cross-table links: ``{source_collection: {column: target_collection}}``.
# When a row in ``source_collection`` is rendered, cells in ``column`` become
# clickable links to ``/hub/c/<target_collection>?q=<cell value>`` — i.e. they
# jump to the target table with that value pre-filled in its search box. The
# value must match one of the target collection's ``search_cols`` for the
# pre-filter to land. Exposed to the row browser via ``list_rows`` → meta.
_CROSS_LINKS: dict[str, dict[str, str]] = {
    # Zenodo record → its community: click the community id, land on the
    # communities table filtered to that community.
    "zenodo_records": {
        "primary_community_id": "communities",
    },
    # GitHub repo → its owner's people/org page in the index.
    "github_repos": {
        "owner": "github_users",
    },
    # GitHub org → the repos it owns (search github_repos by owner login).
    "github_organizations": {
        "login": "github_repos",
    },
}


def _build_auto_backing(collection: str) -> Backing | None:
    """Construct a ``Backing`` for ``collection`` by sniffing the DuckDB schema.

    Returns ``None`` if the file is missing or the table can't be read.
    Stats are derived from ``_AUTO_STAT_PATTERNS`` against the available
    columns; the first matching pattern fills each slot (after Total).
    """
    pair = _AUTO_TABLES.get(collection)
    if pair is None:
        return None
    db_path, table = pair
    if not db_path.is_file():
        return None

    try:
        with _connect(db_path) as con:
            schema = con.execute(f'PRAGMA table_info("{table}")').fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("auto-backing schema sniff failed for %s: %s", collection, exc)
        return None

    if not schema:
        return None

    # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk).
    cols = [(row[1], (row[2] or "").upper()) for row in schema]
    names = [c[0] for c in cols]
    # JSON / nested types can't go into an ILIKE search — DuckDB raises
    # "Vector::Reference used on vector of different type" and (worse)
    # poisons the read-only parent connection for the whole pool. Limit
    # priority + fallback to scalar types only. Plain JSON_COL stays
    # browsable in the row view (it's rendered as text); we just don't
    # let the search box hit it.
    _searchable = {
        n.lower(): n
        for n, t in cols
        if not (t.startswith("JSON") or t.startswith("STRUCT")
                or t.startswith("LIST") or t.startswith("MAP")
                or t.startswith("UNION"))
    }
    lowered = {c.lower(): c for c in names}

    # Optional WHERE clause that scopes the Backing to a regional /
    # institutional subset of the underlying table. Threaded into
    # ``Backing.select_sql`` below so ``_source_expr`` picks it up
    # everywhere — row count, paginated rows endpoint, export, etc.
    filter_clause = _AUTO_FILTERS.get(collection)

    # Stats — always Total, then up to 3 more from the pattern table.
    # Apply the filter to the Total counter so the in-tile stats card
    # matches the home-page tile count.
    total_sql = f'SELECT COUNT(*) FROM "{table}"'
    if filter_clause:
        total_sql += f" WHERE {filter_clause}"
    stats: list[Stat] = [Stat(f"Total {table}", total_sql)]
    used: set[str] = set()
    for label, opts, tmpl, unit in _AUTO_STAT_PATTERNS:
        if len(stats) >= 4:
            break
        for opt in opts:
            real = lowered.get(opt)
            if real and real not in used:
                used.add(real)
                stats.append(Stat(label, tmpl.format(c=real, t=table), unit=unit))
                break

    # Search columns — prioritised text-ish names, capped at 4. We
    # source from ``_searchable`` (scalar columns only) so a column
    # like snsf.persons.responsible_applicant_grants (JSON) never
    # ends up under an ILIKE — see the comment above.
    search_cols: list[str] = []
    for p in _AUTO_SEARCH_PRIORITY:
        real = _searchable.get(p)
        if real and real not in search_cols:
            search_cols.append(real)
        if len(search_cols) >= 4:
            break
    if not search_cols:
        # Fallback: any VARCHAR/TEXT columns.
        search_cols = [
            n for n, t in cols if t.startswith(("VARCHAR", "TEXT", "STRING"))
        ][:4]

    hidden = tuple(n for n in names if _is_hidden_col(n))

    # When a filter is in play, wrap the bare table in a SELECT so the
    # generic ``_source_expr`` path applies the WHERE everywhere it
    # matters (row_count, /rows pagination, /export). Without this
    # wrap, every regional flavour of a shared-table collection reports
    # the same worldwide row count.
    select_sql_override: str | None = None
    if filter_clause:
        select_sql_override = f'SELECT * FROM "{table}" WHERE {filter_clause}'

    return Backing(
        db_path=db_path,
        table=table,
        hidden_cols=hidden,
        stats=tuple(stats),
        search_cols=tuple(search_cols),
        search_examples=_AUTO_SEARCH_EXAMPLES.get(collection, ()),
        select_sql=select_sql_override,
    )


# NOTE: the actual merge into ``_BACKING`` happens at the bottom of
# this module, after ``_connect`` is defined.


# Row-count cache — keyed by ``(db_path, table)``. Invalidated only on
# hub restart, which is acceptable here: a DuckDB table that changes
# also gets a manual swap of the ``.duckdb`` file, and we tell the
# operator to restart the hub for those swaps to fully apply (it
# already needs that for schema changes anyway).
_COUNT_CACHE: dict[tuple[str, str], int] = {}
_COUNT_LOCK = threading.Lock()


def is_browsable(collection: str) -> bool:
    """True when the collection has a registered DuckDB backing."""
    return collection in _BACKING


def backing_for(collection: str) -> Backing | None:
    """Return the backing record or ``None`` if the collection isn't registered."""
    return _BACKING.get(collection)


def row_count_for(collection: str) -> int | None:
    """Public wrapper around the cached row counter.

    Returns the underlying DuckDB row count for a browsable collection,
    or ``None`` if the collection isn't registered / its backing file
    is missing. Used by the hub home tiles to display the
    source-of-truth entity count instead of the (chunk-inflated)
    Qdrant points count.
    """
    b = _BACKING.get(collection)
    if b is None:
        return None
    try:
        return _row_count(b)
    except Exception as exc:  # noqa: BLE001
        log.warning("row_count_for(%s) failed: %s", collection, exc)
        return None


def fresh_row_count(collection: str) -> int | None:
    """Uncached row count for a browsable collection.

    Same backing resolution as :func:`row_count_for` — including the
    ``.ro.duckdb`` snapshot preference and any ``select_sql`` filter — but
    bypasses ``_COUNT_CACHE`` and re-runs ``COUNT(*)`` on every call. Used
    by callers that sample the same collection over time (the overview's
    60s growth chart), where the process-lifetime cache would otherwise
    freeze the series at its first reading.
    """
    b = _BACKING.get(collection)
    if b is None:
        return None
    try:
        with _connect(b.db_path) as con:
            n = con.execute(f"SELECT COUNT(*) FROM {_source_expr(b)}").fetchone()[0]
        return int(n)
    except Exception as exc:  # noqa: BLE001
        log.warning("fresh_row_count(%s) failed: %s", collection, exc)
        return None


def _json_safe(v: Any) -> Any:
    """Coerce a DuckDB cell value into something the JSON encoder can handle.

    DuckDB returns ``datetime``, ``date``, ``Decimal``, ``bytes`` and
    sometimes already-decoded ``dict``/``list`` for ``JSON`` columns.
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        # First few bytes only — these are usually embeddings (4-byte
        # floats), not human-readable content.
        return f"<{len(v)} bytes>"
    if isinstance(v, (list, tuple, dict)):
        return v
    return str(v)


def _snapshot_path(db_path: Path) -> Path:
    """The GME-published read-only snapshot beside a live store.

    ``…/openalex.duckdb`` → ``…/openalex.ro.duckdb`` (see
    ``src/index/_snapshot.py`` in the git-metadata-extractor).
    """
    return db_path.with_name(db_path.stem + ".ro.duckdb")


def _connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    # Prefer the GME-published read-only snapshot. The live ``.duckdb`` is
    # held read-WRITE by the extractor, so opening it read-only from here
    # fails with "Conflicting lock is held" (DuckDB allows N readers OR 1
    # writer). The ``.ro.duckdb`` snapshot is a separate file the GME
    # refreshes after each ingest — no contention. Fall back to the live
    # file when no snapshot exists yet (fresh deploy / un-snapshotted store).
    ro_path = _snapshot_path(db_path)
    target = ro_path if ro_path.is_file() else db_path
    if not target.is_file():
        raise FileNotFoundError(f"DuckDB file missing: {target}")
    return duckdb.connect(str(target), read_only=True)


def _source_expr(b: Backing) -> str:
    """The SQL fragment usable after ``FROM`` for this backing.

    Either a plain ``"<table>"`` identifier or a wrapped subquery
    (when ``b.select_sql`` overrides the source — e.g. zenodo_records
    joining ``record_communities`` to expose a per-record community
    list). The wrapping ``_t`` alias gives both branches the same
    callable shape for downstream ``SELECT … WHERE … LIMIT …`` use.
    """
    if b.select_sql:
        return f"({b.select_sql}) AS _t"
    return f'"{b.table}"'


def _row_count(b: Backing) -> int:
    # Cache key includes ``select_sql`` so collections that share the
    # same underlying ``(db_path, table)`` but apply different WHERE
    # filters (e.g. the ROR regional flavours) each get their own
    # cached count instead of stomping on each other's value.
    key = (str(b.db_path), b.table, b.select_sql or "")
    with _COUNT_LOCK:
        cached = _COUNT_CACHE.get(key)
    if cached is not None:
        return cached
    with _connect(b.db_path) as con:
        # Table name (or wrapped subquery) is hardcoded in the
        # mapping; quoting via double-quote identifiers is safe.
        n = con.execute(f"SELECT COUNT(*) FROM {_source_expr(b)}").fetchone()[0]
    n = int(n)
    with _COUNT_LOCK:
        _COUNT_CACHE[key] = n
    return n


MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


def _format_stat(value: Any, unit: str) -> str:
    """Render a stat scalar according to its unit hint."""
    if value is None:
        return "—"
    if unit == "pct":
        try:
            return f"{float(value):.1f}%"
        except (TypeError, ValueError):
            return str(value)
    if unit == "int":
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def top_stats(collection: str) -> list[dict[str, str]] | None:
    """Run the per-collection scalar stat queries and return ``[{label, value}, ...]``.

    None when the collection isn't registered. Empty list when the
    backing has no ``stats`` configured.
    """
    b = _BACKING.get(collection)
    if b is None:
        return None
    if not b.stats:
        return []
    out: list[dict[str, str]] = []
    con = _connect(b.db_path)
    try:
        for stat in b.stats:
            try:
                row = con.execute(stat.sql).fetchone()
                raw = row[0] if row else None
                value = _format_stat(raw, stat.unit)
            except Exception as exc:  # noqa: BLE001
                log.warning("stat %r failed on %s: %s", stat.label, collection, exc)
                value = "—"
                # Some DuckDB INTERNAL/FATAL errors (notably the upstream
                # Vector::Reference bug that fires on auto-stat
                # COUNT(DISTINCT type) over snsf.output_publications)
                # poison the whole connection — every subsequent
                # execute() on it raises "database has been invalidated".
                # Reopen so the remaining stats don't all return "—".
                msg = str(exc)
                if "invalidated" in msg or "FATAL" in msg:
                    try:
                        con.close()
                    except Exception:  # noqa: BLE001
                        pass
                    con = _connect(b.db_path)
            out.append({"label": stat.label, "value": value})
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def search_info(collection: str) -> dict[str, Any] | None:
    """Surface the search-bar metadata (cols + example chips) for the UI."""
    b = _BACKING.get(collection)
    if b is None:
        return None
    return {
        "enabled": bool(b.search_cols),
        "columns": list(b.search_cols),
        "examples": list(b.search_examples),
    }


def _build_filter(b: Backing, q: str) -> tuple[str, list[Any]]:
    """Build the parameterised WHERE clause used by browse + export."""
    if not q or not b.search_cols:
        return "", []
    like = f"%{q}%"
    params: list[Any] = []
    clauses: list[str] = []
    for col in b.search_cols:
        clauses.append(f'CAST("{col}" AS VARCHAR) ILIKE ?')
        params.append(like)
    return "WHERE " + " OR ".join(clauses), params


def _build_order(sort: str, visible_cols: list[str]) -> str:
    """Validate ``sort`` against ``visible_cols`` and return an ORDER BY clause.

    Format: ``"colname"`` (asc) or ``"colname:desc"``. Returns ``""``
    when no/invalid sort. Column names are looked up in the (hardcoded)
    visible column list, so the value never reaches SQL by string
    interpolation without validation.
    """
    if not sort:
        return ""
    col, _, raw_dir = sort.partition(":")
    col = col.strip()
    direction = (raw_dir or "asc").strip().lower()
    if direction not in {"asc", "desc"}:
        direction = "asc"
    if col not in visible_cols:
        return ""
    return f'ORDER BY "{col}" {direction.upper()} NULLS LAST'


def _resolve_visible_cols(
    con: duckdb.DuckDBPyConnection, b: Backing
) -> tuple[list[str], list[str]]:
    """Return ``(all_cols, visible_cols)`` for the backing's source.

    Uses ``DESCRIBE "<table>"`` for plain-table backings; for those
    wrapping a ``select_sql`` we ``EXPLAIN``-style probe the wrapped
    query with ``LIMIT 0`` to get cursor descriptions — DESCRIBE
    doesn't accept arbitrary subqueries.
    """
    if b.select_sql:
        cur = con.execute(f"SELECT * FROM {_source_expr(b)} LIMIT 0")
        all_cols = [d[0] for d in (cur.description or [])]
    else:
        all_cols = [c[0] for c in con.execute(f'DESCRIBE "{b.table}"').fetchall()]
    # Reflect ALL columns in the row browser (the row tables carry no
    # high-dimensional vector columns — embeddings live in Qdrant / the
    # ``chunks`` table — so showing every column is safe and is what the
    # browser is expected to surface). ``hidden_cols`` is kept on the
    # Backing for other consumers but no longer drops columns from view.
    visible = list(all_cols)
    return all_cols, visible


def list_rows(
    collection: str,
    *,
    page: int = 1,
    size: int = DEFAULT_PAGE_SIZE,
    q: str = "",
    sort: str = "",
    filters: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Return a paginated slice of the DuckDB table behind ``collection``.

    Returns ``None`` when the collection isn't a known browsable surface.
    Otherwise returns ``{collection, db_path, table, columns, all_columns,
    hidden, rows, page, size, total, pages, q, sort, matched}``.

    ``q`` is a case-insensitive substring filter applied to every column
    in :attr:`Backing.search_cols`. When non-empty the row count + total
    pages reflect the filtered set; ``matched`` is the filtered count.

    ``sort`` is ``"col"`` (asc) or ``"col:desc"``. Invalid columns are
    silently ignored.

    ``filters`` is an optional ``{column: value}`` map of exact (case-
    insensitive) equality constraints, AND-combined with ``q``. Columns
    not present in the table are silently ignored (validated against the
    real schema, so the column name never reaches SQL unchecked).
    """
    b = _BACKING.get(collection)
    if b is None:
        return None

    page = max(1, int(page or 1))
    size = max(1, min(MAX_PAGE_SIZE, int(size or DEFAULT_PAGE_SIZE)))
    q = (q or "").strip()
    sort = (sort or "").strip()
    filters = filters or {}
    offset = (page - 1) * size

    src = _source_expr(b)
    with _connect(b.db_path) as con:
        all_cols, visible_cols = _resolve_visible_cols(con, b)

        where_sql, params_filter = _build_filter(b, q)
        # AND-combine exact-match filters on validated columns.
        extra_clauses: list[str] = []
        extra_params: list[Any] = []
        for col, val in filters.items():
            if col in all_cols and str(val).strip():
                extra_clauses.append(f'CAST("{col}" AS VARCHAR) ILIKE ?')
                extra_params.append(str(val).strip())
        if extra_clauses:
            joiner = " AND " if where_sql else "WHERE "
            where_sql = (where_sql or "") + joiner + " AND ".join(extra_clauses)
            params_filter = [*params_filter, *extra_params]

        if where_sql:
            matched = int(
                con.execute(
                    f"SELECT COUNT(*) FROM {src} {where_sql}", params_filter
                ).fetchone()[0]
            )
        else:
            matched = _row_count(b)
        total = _row_count(b)
        pages = max(1, (matched + size - 1) // size)

        col_list = ", ".join(f'"{c}"' for c in visible_cols)
        order_sql = _build_order(sort, visible_cols)

        rows = con.execute(
            f"SELECT {col_list} FROM {src} {where_sql} {order_sql} LIMIT ? OFFSET ?",
            [*params_filter, size, offset],
        ).fetchall()

    rows_out = [
        {col: _json_safe(val) for col, val in zip(visible_cols, row)} for row in rows
    ]
    return {
        "collection": collection,
        "db_path": str(b.db_path),
        "table": b.table,
        "columns": visible_cols,
        "all_columns": all_cols,
        "hidden": list(b.hidden_cols),
        "cross_links": _CROSS_LINKS.get(collection, {}),
        "rows": rows_out,
        "page": page,
        "size": size,
        "total": total,
        "matched": matched,
        "pages": pages,
        "q": q,
        "sort": sort,
    }


# Cap on how many rows a single ``/export`` call may return. Picked
# conservatively so a single click can't materialise a 100MB payload
# in memory. The UI shows a "(truncated)" hint when this kicks in.
MAX_EXPORT_ROWS = 50_000


def export_rows(
    collection: str,
    *,
    q: str = "",
    sort: str = "",
    limit: int = MAX_EXPORT_ROWS,
) -> dict[str, Any] | None:
    """Fetch the full filtered+sorted dataset for export — no pagination.

    Returns ``{columns, rows, matched, truncated, table}`` or ``None``
    when the collection isn't registered.
    """
    b = _BACKING.get(collection)
    if b is None:
        return None

    q = (q or "").strip()
    sort = (sort or "").strip()
    limit = max(1, min(int(limit or MAX_EXPORT_ROWS), MAX_EXPORT_ROWS))

    where_sql, params_filter = _build_filter(b, q)
    src = _source_expr(b)

    with _connect(b.db_path) as con:
        if where_sql:
            matched = int(
                con.execute(
                    f"SELECT COUNT(*) FROM {src} {where_sql}", params_filter
                ).fetchone()[0]
            )
        else:
            matched = _row_count(b)

        _, visible_cols = _resolve_visible_cols(con, b)
        col_list = ", ".join(f'"{c}"' for c in visible_cols)
        order_sql = _build_order(sort, visible_cols)

        rows = con.execute(
            f"SELECT {col_list} FROM {src} {where_sql} {order_sql} LIMIT ?",
            [*params_filter, limit + 1],
        ).fetchall()

    truncated = len(rows) > limit
    rows = rows[:limit]
    rows_out = [[_json_safe(v) for v in row] for row in rows]
    return {
        "columns": visible_cols,
        "rows": rows_out,
        "matched": matched,
        "truncated": truncated,
        "table": b.table,
    }


def _csv_cell(v: Any) -> str:
    """RFC-4180 cell encoding — same rules the /databases page uses."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple, dict)):
        s = json.dumps(v, ensure_ascii=False, default=str)
    else:
        s = str(v)
    if any(c in s for c in ('"', ",", "\n", "\r")):
        s = '"' + s.replace('"', '""') + '"'
    return s


def _tsv_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple, dict)):
        s = json.dumps(v, ensure_ascii=False, default=str)
    else:
        s = str(v)
    # Strip TSV-breaking whitespace.
    return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")


def _md_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple, dict)):
        s = json.dumps(v, ensure_ascii=False, default=str)
    else:
        s = str(v)
    return s.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_export(collection: str, fmt: str, **kwargs: Any) -> tuple[str, str] | None:
    """Render the export payload for a given format.

    Returns ``(body, mime)`` or ``None`` if the collection / format is
    unknown. Supported formats: ``csv``, ``tsv``, ``md``, ``json-rec``,
    ``json-col``.
    """
    payload = export_rows(collection, **kwargs)
    if payload is None:
        return None
    cols: list[str] = payload["columns"]
    rows: list[list[Any]] = payload["rows"]

    if fmt == "csv":
        lines = [",".join(_csv_cell(c) for c in cols)]
        lines.extend(",".join(_csv_cell(v) for v in row) for row in rows)
        return "\n".join(lines) + "\n", "text/csv; charset=utf-8"
    if fmt == "tsv":
        lines = ["\t".join(_tsv_cell(c) for c in cols)]
        lines.extend("\t".join(_tsv_cell(v) for v in row) for row in rows)
        return "\n".join(lines) + "\n", "text/tab-separated-values; charset=utf-8"
    if fmt == "md":
        head = "| " + " | ".join(_md_cell(c) for c in cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        body = ["| " + " | ".join(_md_cell(v) for v in row) + " |" for row in rows]
        return "\n".join([head, sep, *body]) + "\n", "text/markdown; charset=utf-8"
    if fmt == "json-rec":
        records = [{c: v for c, v in zip(cols, row)} for row in rows]
        return json.dumps(
            records, ensure_ascii=False, default=str, indent=2
        ), "application/json; charset=utf-8"
    if fmt == "json-col":
        columnar: dict[str, list[Any]] = {c: [] for c in cols}
        for row in rows:
            for c, v in zip(cols, row):
                columnar[c].append(v)
        return json.dumps(
            columnar, ensure_ascii=False, default=str, indent=2
        ), "application/json; charset=utf-8"

    return None


# Final step at module import: enrich ``_BACKING`` with auto-derived
# entries for every collection in ``_AUTO_TABLES`` that doesn't already
# have a hand-tuned record. Done at the bottom so all helpers used by
# ``_build_auto_backing`` (notably ``_connect``) are already defined.
for _coll in _AUTO_TABLES:
    if _coll not in _BACKING:
        _auto = _build_auto_backing(_coll)
        if _auto is not None:
            _BACKING[_coll] = _auto
