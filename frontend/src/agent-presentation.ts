import type { AgentOption, AgentType } from './client.js';

export function agentLabel(options: AgentOption[], agentType: AgentType): string {
  return options.find((agent) => agent.agent_type === agentType)?.label
    ?? (agentType === 'foundry-prompt' ? 'Foundry Prompt Agent' : 'Hosted Agent Framework');
}

export function agentDescription(agentType: AgentType): string {
  return agentType === 'foundry-prompt'
    ? 'Knowledge-only runtime for grounded policy and product answers.'
    : 'Full support runtime with order, profile, and memory tools.';
}

export function agentIcon(agentType: AgentType): string {
  return agentType === 'foundry-prompt' ? 'menu_book' : 'hub';
}
