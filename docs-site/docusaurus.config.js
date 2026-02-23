// @ts-check

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: "Open Pulse Docs",
  tagline: "Documentation source of truth",

  url: "https://example.com",
  baseUrl: "/",

  organizationName: "open-pulse",
  projectName: "open-pulse-1",

  onBrokenLinks: "throw",
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: "throw"
    }
  },

  i18n: {
    defaultLocale: "en",
    locales: ["en"]
  },

  presets: [
    [
      "classic",
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          sidebarPath: require.resolve("./sidebars.js"),
          routeBasePath: "/"
        },
        blog: false,
        pages: false,
        theme: {
          customCss: require.resolve("./src/css/custom.css")
        }
      })
    ]
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      navbar: {
        title: "Open Pulse Docs",
        items: [
          { to: "/getting-started", label: "Getting Started", position: "left" },
          { to: "/operations/branch-model", label: "Branch Model", position: "left" }
        ]
      }
    })
};

module.exports = config;
