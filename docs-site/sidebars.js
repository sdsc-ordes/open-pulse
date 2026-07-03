/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  docsSidebar: [
    "index",
    "getting-started/index",
    "architecture/index",
    {
      type: "category",
      label: "Concepts",
      items: [
        "concepts/graph-and-semantic-data",
        "concepts/metadata-and-ontology",
        "concepts/metrics-and-chaoss"
      ]
    },
    "hub/index",
    "pipeline/index",
    "reference/chaoss-api",
    "reference/access-control",
    "use-cases/index",
    {
      type: "category",
      label: "Operations",
      items: [
        "operations/deployment",
        "operations/activity-tracking",
        "operations/register-a-node",
        "operations/release-checklist",
        "operations/branch-model"
      ]
    },
    "contributing/index",
    "community/index"
  ]
};

module.exports = sidebars;
