import type { SetupStatus } from '../shared/types.js';
import {
  configuredProviders,
  defaultModelRef,
  parseModelRef,
  providerKey,
  providerKeyName,
  workerModelRef,
} from './models.js';
export interface PlatformConfig {
  intelligenceKey?: string;
  intelligenceApiUrl?: string;
  intelligenceWsUrl?: string;
  /** Legacy OpenAI default model (no provider prefix). */
  model?: string;
  /** OpenAI (or compatible) key. */
  apiKey?: string;
  baseUrl: string;
  anthropicKey?: string;
  anthropicBaseUrl?: string;
  openrouterKey?: string;
  openrouterBaseUrl?: string;
  /** `provider:model` used by Dots without their own model. */
  defaultModel?: string;
  /** `provider:model` suggested for specialist Dots. */
  workerModel?: string;
  /** IANA time zone used for new cron schedules. */
  timezone?: string;
  computerSupervisorUrl?: string;
  computerSupervisorToken?: string;
  computerToken?: string;
  computerNamespace?: string;
  browserUrl?: string;
  browserSecret?: string;
  voiceKey?: string;
  voiceModel?: string;
  voiceName: string;
  slackChannel?: string;
  slackTeam?: string;
  slackUsers: string[];
  slackDotId?: string;
  runtimeUrl: string;
  ownerToken?: string;
  /** Public base URL used to display webhook addresses. */
  publicUrl?: string;
}
function modelMissing(config: PlatformConfig): string | undefined {
  const value = defaultModelRef(config);
  if (!value) return config.apiKey ? 'OPENAI_MODEL' : 'DEFAULT_MODEL';
  try {
    const ref = parseModelRef(value);
    return providerKey(config, ref.provider)
      ? undefined
      : providerKeyName[ref.provider];
  } catch {
    return 'DEFAULT_MODEL';
  }
}
export function setupStatus(
  config: PlatformConfig,
  slack = 'not_configured',
  activationFailed = false,
): SetupStatus {
  const missingModel = modelMissing(config);
  const missing = [
    !config.intelligenceKey && 'INTELLIGENCE_API_KEY',
    missingModel,
  ].filter((item): item is string => !!item);
  const declaredSlack = !!(
    config.slackChannel &&
    config.slackTeam &&
    config.slackUsers.length
  );
  slack = declaredSlack
    ? activationFailed && slack !== 'online'
      ? 'activation_failed'
      : slack
    : config.slackChannel || config.slackTeam || config.slackUsers.length
      ? 'setup_required'
      : 'not_configured';
  return {
    intelligence: !!config.intelligenceKey,
    model: !missingModel,
    browser: !!(config.browserUrl && config.browserSecret),
    voice: !!(config.voiceKey && config.voiceModel && !missing.length),
    slack,
    missing,
    providers: configuredProviders(config),
    defaultModel: defaultModelRef(config) ?? null,
    workerModel: workerModelRef(config) ?? null,
    timezone: config.timezone ?? 'UTC',
  };
}
