// @ts-check

const OPEN_PULSE_VERSION = "1.0.0";

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: "Open Pulse",
  tagline: "Monitor the health of your open-source ecosystem.",

  url: "https://example.com",
  baseUrl: "/",

  organizationName: "open-pulse",
  projectName: "open-pulse-1",

  customFields: {
    openPulseVersion: OPEN_PULSE_VERSION
  },

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
          routeBasePath: "/docs"
        },
        blog: false,
        theme: {
          customCss: require.resolve("./src/css/custom.css")
        }
      })
    ]
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      announcementBar: {
        id: "v1-launch",
        content:
          '🎉 <b>Open Pulse v' + OPEN_PULSE_VERSION + '</b> is live — ' +
          'try the hosted instance at <a target="_blank" rel="noopener" href="https://openpulse.epfl.ch">openpulse.epfl.ch</a> ' +
          'or <code>pip install open-pulse-science</code>.',
        backgroundColor: "#1e6bb8",
        textColor: "#ffffff",
        isCloseable: true
      },
      navbar: {
        title: "Open Pulse",
        items: [
          { to: "/docs/getting-started", label: "Getting Started", position: "left" },
          { to: "/docs/operations/branch-model", label: "Branch Model", position: "left" },
          {
            to: "/docs",
            label: "Docs · v" + OPEN_PULSE_VERSION,
            position: "right",
            className: "navbar__item--docs-cta"
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
