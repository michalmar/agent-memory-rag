// Declarative A2UI template for order-status tool results.
import type { ComponentDef } from '../a2ui/types.js';

export const SHIPPING_STATUS_TEMPLATE: ComponentDef[] = [
  { id: 'root', component: { Card: { child: 'main-column' } } },
  {
    id: 'main-column',
    component: {
      Column: {
        gap: 'medium',
        children: {
          explicitList: ['header', 'tracking-number', 'divider', 'steps', 'eta'],
        },
      },
    },
  },
  {
    id: 'header',
    component: {
      Row: {
        alignment: 'center',
        gap: 'small',
        children: { explicitList: ['package-icon', 'title'] },
      },
    },
  },
  { id: 'package-icon', component: { Icon: { name: { literalString: 'package_2' } } } },
  {
    id: 'title',
    component: { Text: { text: { literalString: 'Package Status' }, usageHint: 'h3' } },
  },
  {
    id: 'tracking-number',
    component: { Text: { text: { path: '/trackingNumber' }, usageHint: 'caption' } },
  },
  { id: 'divider', component: { Divider: {} } },
  {
    id: 'steps',
    component: {
      Column: {
        gap: 'small',
        children: { explicitList: ['step1', 'step2', 'step3', 'step4'] },
      },
    },
  },
  {
    id: 'step1',
    component: {
      Row: {
        alignment: 'center',
        gap: 'small',
        children: { explicitList: ['step1-icon', 'step1-text'] },
      },
    },
  },
  { id: 'step1-icon', component: { Icon: { name: { literalString: 'check_circle' } } } },
  {
    id: 'step1-text',
    component: { Text: { text: { literalString: 'Order Placed' }, usageHint: 'body' } },
  },
  {
    id: 'step2',
    component: {
      Row: {
        alignment: 'center',
        gap: 'small',
        children: { explicitList: ['step2-icon', 'step2-text'] },
      },
    },
  },
  { id: 'step2-icon', component: { Icon: { name: { literalString: 'check_circle' } } } },
  {
    id: 'step2-text',
    component: { Text: { text: { literalString: 'Shipped' }, usageHint: 'body' } },
  },
  {
    id: 'step3',
    component: {
      Row: {
        alignment: 'center',
        gap: 'small',
        children: { explicitList: ['step3-icon', 'step3-text'] },
      },
    },
  },
  { id: 'step3-icon', component: { Icon: { name: { path: '/currentStepIcon' } } } },
  {
    id: 'step3-text',
    component: { Text: { text: { literalString: 'Out for Delivery' }, usageHint: 'h4' } },
  },
  {
    id: 'step4',
    component: {
      Row: {
        alignment: 'center',
        gap: 'small',
        children: { explicitList: ['step4-icon', 'step4-text'] },
      },
    },
  },
  { id: 'step4-icon', component: { Icon: { name: { literalString: 'circle' } } } },
  {
    id: 'step4-text',
    component: { Text: { text: { literalString: 'Delivered' }, usageHint: 'caption' } },
  },
  {
    id: 'eta',
    component: {
      Row: {
        alignment: 'center',
        gap: 'small',
        children: { explicitList: ['eta-icon', 'eta-text'] },
      },
    },
  },
  { id: 'eta-icon', component: { Icon: { name: { literalString: 'schedule' } } } },
  {
    id: 'eta-text',
    component: { Text: { text: { path: '/eta' }, usageHint: 'body' } },
  },
];
