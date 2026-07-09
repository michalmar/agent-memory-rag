// RAG_CITATIONS_TEMPLATE — verbatim ComponentDef[] (see PRD §B8).
import type { ComponentDef } from '../a2ui/types.js';

export const RAG_CITATIONS_TEMPLATE: ComponentDef[] = [
  { id: 'root', component: { Card: { child: 'main-col' } } },
  {
    id: 'main-col',
    component: {
      Column: {
        gap: 'medium',
        children: { explicitList: ['header', 'divider', 'citation-list'] },
      },
    },
  },
  {
    id: 'header',
    component: {
      Row: {
        alignment: 'center',
        gap: 'small',
        children: { explicitList: ['header-icon', 'header-title', 'header-count'] },
      },
    },
  },
  { id: 'header-icon', component: { Icon: { name: { literalString: 'menu_book' } } } },
  {
    id: 'header-title',
    component: { Text: { text: { literalString: 'Sources' }, usageHint: 'h3' } },
  },
  {
    id: 'header-count',
    component: { Text: { text: { path: '/citationCount' }, usageHint: 'caption' } },
  },
  { id: 'divider', component: { Divider: {} } },
  {
    id: 'citation-list',
    component: {
      List: {
        direction: 'vertical',
        children: { template: { dataBinding: '/citations', componentId: 'citation-row' } },
      },
    },
  },
  {
    id: 'citation-row',
    component: {
      Row: {
        alignment: 'start',
        gap: 'small',
        children: { explicitList: ['cite-icon', 'cite-details'] },
      },
    },
  },
  { id: 'cite-icon', component: { Icon: { name: { literalString: 'description' } } } },
  {
    id: 'cite-details',
    component: {
      Column: {
        gap: 'small',
        children: { explicitList: ['cite-source', 'cite-snippet'] },
      },
    },
  },
  {
    id: 'cite-source',
    component: { Text: { text: { path: 'sourceName' }, usageHint: 'h5' } },
  },
  {
    id: 'cite-snippet',
    component: { Text: { text: { path: 'snippet' }, usageHint: 'caption' } },
  },
];
