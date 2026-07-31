export type ApplicationHealthStatus =
  | 'checking'
  | 'healthy'
  | 'degraded'
  | 'unhealthy';

export interface DependencyHealth {
  status: 'ok' | 'failed';
  error?: string;
  required?: boolean;
}

export interface HealthResponse {
  status: 'ready' | 'not_ready';
  dependencies: Record<string, DependencyHealth>;
  degraded_dependencies?: string[];
}

export interface ApplicationHealthSummary {
  status: ApplicationHealthStatus;
  label: string;
  detail: string;
}

export const CHECKING_HEALTH: ApplicationHealthSummary = {
  status: 'checking',
  label: 'Checking',
  detail: 'Checking application health',
};

export const UNAVAILABLE_HEALTH: ApplicationHealthSummary = {
  status: 'unhealthy',
  label: 'Unhealthy',
  detail: 'Application health check is unavailable',
};

export function summarizeHealth(
  response: HealthResponse,
): ApplicationHealthSummary {
  const requiredFailures = Object.entries(response.dependencies)
    .filter(([, dependency]) =>
      dependency.required !== false && dependency.status !== 'ok')
    .map(([name]) => name);
  if (response.status !== 'ready' || requiredFailures.length > 0) {
    return {
      status: 'unhealthy',
      label: 'Unhealthy',
      detail: dependencyDetail('Required dependency failure', requiredFailures),
    };
  }

  const degraded = new Set(response.degraded_dependencies ?? []);
  Object.entries(response.dependencies)
    .filter(([, dependency]) =>
      dependency.required === false && dependency.status !== 'ok')
    .forEach(([name]) => degraded.add(name));
  if (degraded.size > 0) {
    return {
      status: 'degraded',
      label: 'Degraded',
      detail: dependencyDetail('Optional dependency failure', [...degraded]),
    };
  }

  return {
    status: 'healthy',
    label: 'Healthy',
    detail: 'All required application dependencies are healthy',
  };
}

function dependencyDetail(prefix: string, dependencies: string[]): string {
  return dependencies.length > 0
    ? `${prefix}: ${dependencies.join(', ')}`
    : prefix;
}
