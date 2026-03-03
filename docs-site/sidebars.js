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
      label: "Operations",
      items: [
        "operations/index",
        "operations/branch-model",
        "operations/migration-from-static-docs"
      ]
    }
  ]
};

module.exports = sidebars;
