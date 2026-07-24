import type { AgentOption, AgentType } from './client.js';

const AGENT_PRESENTATION: Record<
  AgentType,
  { label: string; description: string; icon: string }
> = {
  'foundry-prompt': {
    label: 'Foundry Prompt Agent',
    description: 'Knowledge-only runtime for grounded policy and product answers.',
    icon: 'menu_book',
  },
  'agent-framework': {
    label: 'Hosted Agent Framework',
    description: 'Full support runtime with order, profile, and memory tools.',
    icon: 'hub',
  },
  'directive-rag': {
    label: 'Directive Assistant',
    description: 'Long-form search, summaries, comparisons, and directive guidance.',
    icon: 'policy',
  },
};

export function agentLabel(options: AgentOption[], agentType: AgentType): string {
  return options.find((agent) => agent.agent_type === agentType)?.label
    ?? AGENT_PRESENTATION[agentType].label;
}

export function agentDescription(agentType: AgentType): string {
  return AGENT_PRESENTATION[agentType].description;
}

export function agentIcon(agentType: AgentType): string {
  return AGENT_PRESENTATION[agentType].icon;
}
