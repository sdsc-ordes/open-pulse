// @ts-check

// Env-driven baseUrl so the same config works locally (default "/docs/")
// and on GitHub Pages where the site lives under /open-pulse/docs/.
const baseUrl = process.env.DOCS_BASE_URL || "/docs/";
// The landing lives one level above the docs (consolidated under docs/).
const landingHref = baseUrl.replace(/\/docs\/$/, "/");

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: "Open Pulse Docs",
  tagline: "Open-source ecosystem monitoring — documentation",

  url: "https://sdsc-ordes.github.io",
  baseUrl,

  organizationName: "sdsc-ordes",
  projectName: "open-pulse",

  onBrokenLinks: "throw",
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: "throw"
    }
  },

  // Match the landing's typography (Manrope + JetBrains Mono).
  headTags: [
    {
      tagName: "link",
      attributes: { rel: "preconnect", href: "https://fonts.googleapis.com" }
    },
    {
      tagName: "link",
      attributes: { rel: "preconnect", href: "https://fonts.gstatic.com", crossorigin: "true" }
    },
    {
      tagName: "link",
      attributes: {
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Manrope:wght@400;500;600;700&display=swap"
      }
    }
  ],

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
          { to: "/operations/branch-model", label: "Branch Model", position: "left" },
          {
            href: landingHref,
            label: "← Open Pulse",
            position: "right"
          },
          {
            href: "https://github.com/sdsc-ordes/open-pulse",
            label: "GitHub",
            position: "right"
          }
        ]
      }
    })
};

module.exports = config;
