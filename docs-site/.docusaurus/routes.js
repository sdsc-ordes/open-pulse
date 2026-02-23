import React from 'react';
import ComponentCreator from '@docusaurus/ComponentCreator';

export default [
  {
    path: '/',
    component: ComponentCreator('/', 'dbd'),
    routes: [
      {
        path: '/',
        component: ComponentCreator('/', '573'),
        routes: [
          {
            path: '/',
            component: ComponentCreator('/', 'af8'),
            routes: [
              {
                path: '/analysis',
                component: ComponentCreator('/analysis', '01a'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/architecture',
                component: ComponentCreator('/architecture', '5b4'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/getting-started',
                component: ComponentCreator('/getting-started', 'f92'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/operations',
                component: ComponentCreator('/operations', '1e5'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/operations/branch-model',
                component: ComponentCreator('/operations/branch-model', '804'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/operations/migration-from-static-docs',
                component: ComponentCreator('/operations/migration-from-static-docs', 'fd1'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/services',
                component: ComponentCreator('/services', 'e2c'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/',
                component: ComponentCreator('/', '682'),
                exact: true,
                sidebar: "docsSidebar"
              }
            ]
          }
        ]
      }
    ]
  },
  {
    path: '*',
    component: ComponentCreator('*'),
  },
];
