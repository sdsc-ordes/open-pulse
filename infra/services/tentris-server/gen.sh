#!/usr/bin/env bash
# generate_software_ttl.sh
# Generates a Turtle file with 100,000 schema:SoftwareSourceCode instances.
# Usage: ./generate_software_ttl.sh [output_file]
set -euo pipefail

OUT="${1:-software_instances.ttl}"
COUNT=100000

echo "Writing ${COUNT} software instances to ${OUT} ..."

# Remove existing file
: > "$OUT"

# Write prefixes (unique set, based on the provided schema)
cat >> "$OUT" <<'TTL_HEADER'
@prefix schema: <http://schema.org/> .
@prefix sd: <https://w3id.org/okn/o/sd#> .
@prefix bio: <https://bioschemas.org/> .
@prefix spe: <https://openschemas.github.io/spec-container/specifications/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix shsh: <http://www.w3.org/ns/shacl-shacl#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix pulse: <https://open-pulse.epfl.ch/ontology#> .
@prefix md4i: <http://w3id.org/nfdi4ing/metadata4ing#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix vann: <http://purl.org/vocab/vann/> .
@prefix sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#> .
@prefix dmib: <https://biomedit.ch/rdf/sphn-schema/dmib#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix wd: <http://www.wikidata.org/entity/> .
@prefix rdf4j: <http://rdf4j.org/schema/rdf4j#> .
@prefix sesame: <http://www.openrdf.org/schema/sesame#> .
@prefix fn: <http://www.w3.org/2005/xpath-functions#> .

TTL_HEADER

# Prepare arrays for programming languages and discipline choices.
# These discipline IRIs were present in your pasted schema snippet.
languages=(Python JavaScript "C++" Java R Go Rust MATLAB "C#")
disciplines=(wd:Q34749 wd:Q7112556 wd:Q80083 wd:Q816264 wd:Q7991 wd:Q395 wd:Q420 wd:Q413 wd:Q83404 wd:Q843601)
# Note: Q83404 not in your list? left intentionally to keep variety; if you want only listed IRIs, replace accordingly.

# Iterate and append triples per software instance.
# Use printf and a heredoc-like block to keep turtle formatting readable.
i=1
while [ $i -le $COUNT ]; do
  # deterministic choices based on i
  idx=$(( (i - 1) % ${#languages[@]} ))
  lang="${languages[$idx]}"

  # discipline pick
  didx=$(( (i - 1) % ${#disciplines[@]} ))
  discipline="${disciplines[$didx]}"

  # date generation (deterministic): year 2000..2025, month 1..12, day 1..28
  year=$((2000 + (i % 26)))
  month=$((1 + (i % 12)))
  day=$((1 + (i % 28)))
  # format zero-padded
  printf -v monthp "%02d" "$month"
  printf -v dayp "%02d" "$day"
  date="${year}-${monthp}-${dayp}"

  # URIs and values
  software_uri="http://example.org/software/${i}"
  person_uri="http://example.org/person/${i}"
  repo_url="http://github.com/exampleorg/repo${i}"
  readme_url="http://raw.githubusercontent.com/exampleorg/repo${i}/README.md"
  citation_url="https://doi.org/10.1234/example.${i}"
  download_url="http://example.org/downloads/software${i}.zip"
  webpage_url="http://example.org/software/${i}/"
  identifier="software-${i}"
  license_url="https://spdx.org/licenses/MIT.html"

  # description (short)
  description="Example software instance number ${i} - demo data generated for testing."

  # Write triple block for this software instance
  cat >> "$OUT" <<EOF
<${software_uri}> a schema:SoftwareSourceCode ;
    schema:name "Example Software ${i}" ;
    schema:identifier "${identifier}" ;
    schema:description "${description}" ;
    schema:codeRepository "${repo_url}" ;
    schema:citation "${citation_url}" ;
    schema:dateCreated "${date}"^^xsd:date ;
    schema:datePublished "${date}"^^xsd:date ;
    schema:license <${license_url}> ;
    schema:programmingLanguage "${lang}" ;
    schema:url <${webpage_url}> ;
    sd:readme <${readme_url}> ;
    schema:contentUrl <${download_url}> ;
    pulse:discipline ${discipline} ;
    pulse:repositoryType pulse:Software ;
    schema:author <${person_uri}> .

<${person_uri}> a schema:Person ;
    schema:name "Person ${i}" ;
    schema:givenName "Given${i}" ;
    schema:familyName "Family${i}" .

EOF

  # progress indicator every 10000 (optional)
  if (( i % 10000 == 0 )); then
    echo "  -> generated ${i} ..."
  fi

  i=$((i + 1))
done

echo "Done. Output file: ${OUT}"
