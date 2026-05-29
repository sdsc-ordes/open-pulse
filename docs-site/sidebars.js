/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  docsSidebar: [
    "index",
    "getting-started/index",
    "architecture/index",
    "services/index",
    "analysis/index",
    {
      type: "category",
      label: "Concepts",
      items: [
        "concepts/metadata-and-ontology",
        "concepts/graph-and-semantic-data",
        "concepts/metrics-and-chaoss"
      ]
    },
    "use-cases/index",
    {
      type: "category",
      label: "Operations",
      items: [
        "operations/index",
        "operations/deployment",
        "operations/branch-model",
        "operations/register-a-node",
        "operations/release-checklist",
        "operations/migration-from-static-docs"
      ]
    },
    "contributing/index",
    "community/index"
  ]
};

module.exports = sidebars;
