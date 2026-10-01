import type { AnyTextAdapter } from '@tanstack/ai';
import { createAnthropicChat } from '@tanstack/ai-anthropic';
import { openaiCompatibleText } from '@tanstack/ai-openai/compatible';
import { z } from 'zod';
import type { PlatformConfig } from './platform-config.js';

/**
 * Model routing for Dots. Each Dot may name its own `provider:model`, so a
 * Chief of Staff can run on a strong reasoning model while background
 * specialists run on a cheaper, faster one (the "Astra vs Sol" split).
 */
export const providers = ['openai', 'anthropic', 'openrouter'] as const;
export type Provider = (typeof providers)[number];
export interface ModelRef {
  provider: Provider;
  model: string;
}

const refPattern =
  /^(openai|anthropic|openrouter):([A-Za-z0-9._/:@+-]{1,160})$/;
export const modelRefSchema = z
  .string()
  .trim()
  .max(180)
  .regex(refPattern, 'Use provider:model, e.g. anthropic:claude-sonnet-4-5.');

export const providerKeyName: Record<Provider, string> = {
  openai: 'OPENAI_API_KEY',
  anthropic: 'ANTHROPIC_API_KEY',
  openrouter: 'OPENROUTER_API_KEY',
};

export function parseModelRef(value: string): ModelRef {
  const trimmed = value.trim();
  const match = refPattern.exec(trimmed);
  if (match) return { provider: match[1] as Provider, model: match[2] };
  // Legacy OPENAI_MODEL values have no provider prefix.
  if (!trimmed || trimmed.includes(' '))
    throw new Error('Model must be written as provider:model.');
  return { provider: 'openai', model: trimmed };
}

export const formatModelRef = (ref: ModelRef) => `${ref.provider}:${ref.model}`;

export function defaultModelRef(config: PlatformConfig): string | undefined {
  if (config.defaultModel) return config.defaultModel;
  return config.model ? `openai:${config.model}` : undefined;
}

export function workerModelRef(config: PlatformConfig): string | undefined {
  return config.workerModel || defaultModelRef(config);
}

export function providerKey(
  config: PlatformConfig,
  provider: Provider,
): string | undefined {
  return provider === 'openai'
    ? config.apiKey
    : provider === 'anthropic'
      ? config.anthropicKey
      : config.openrouterKey;
}

export const configuredProviders = (config: PlatformConfig): Provider[] =>
  providers.filter((provider) => !!providerKey(config, provider));

/** Resolve a Dot's model, falling back to the deployment default. */
export function resolveModel(
  config: PlatformConfig,
  override?: string | null,
): ModelRef {
  const value = override || defaultModelRef(config);
  if (!value)
    throw new Error(
      'Setup required: set DEFAULT_MODEL (or OPENAI_MODEL) for your Dots.',
    );
  const ref = parseModelRef(value);
  if (!providerKey(config, ref.provider))
    throw new Error(
      `Setup required: ${providerKeyName[ref.provider]} is needed for ${formatModelRef(ref)}.`,
    );
  return ref;
}

export function textAdapter(
  config: PlatformConfig,
  ref: ModelRef,
  maxTokens = 2200,
): { adapter: AnyTextAdapter; modelOptions: Record<string, unknown> } {
  const apiKey = providerKey(config, ref.provider);
  if (!apiKey)
    throw new Error(`Setup required: ${providerKeyName[ref.provider]}.`);
  if (ref.provider === 'anthropic')
    return {
      adapter: createAnthropicChat(ref.model as never, apiKey, {
        maxRetries: 1,
        ...(config.anthropicBaseUrl
          ? { baseURL: config.anthropicBaseUrl }
          : {}),
      }) as unknown as AnyTextAdapter,
      modelOptions: { max_tokens: maxTokens },
    };
  if (ref.provider === 'openrouter')
    return {
      adapter: openaiCompatibleText(ref.model, {
        apiKey,
        baseURL: config.openrouterBaseUrl ?? 'https://openrouter.ai/api/v1',
        api: 'chat-completions',
        maxRetries: 1,
      }) as unknown as AnyTextAdapter,
      modelOptions: { max_tokens: maxTokens },
    };
  return {
    adapter: openaiCompatibleText(ref.model, {
      apiKey,
      baseURL: config.baseUrl ?? 'https://api.openai.com/v1',
      api: 'chat-completions',
      maxRetries: 1,
    }) as unknown as AnyTextAdapter,
    modelOptions: { max_completion_tokens: maxTokens },
  };
}

/** Short list for the Dot settings picker; any provider:model string works. */
export const suggestedModels = [
  'openai:gpt-5.2',
  'openai:gpt-5-mini',
  'anthropic:claude-opus-4-5',
  'anthropic:claude-sonnet-4-5',
  'anthropic:claude-haiku-4-5',
  'openrouter:anthropic/claude-sonnet-4.5',
  'openrouter:openai/gpt-5-mini',
];
