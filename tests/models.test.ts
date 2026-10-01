import { expect, it } from 'vitest';
import {
  configuredProviders,
  parseModelRef,
  resolveModel,
  textAdapter,
} from '../src/server/models.js';
import {
  setupStatus,
  type PlatformConfig,
} from '../src/server/platform-config.js';

const base: PlatformConfig = {
  intelligenceKey: 'fixture',
  baseUrl: 'https://api.openai.com/v1',
  runtimeUrl: '',
  voiceName: 'marin',
  slackUsers: [],
};

it('parses provider-prefixed and legacy model names', () => {
  expect(parseModelRef('anthropic:claude-sonnet-4-5')).toEqual({
    provider: 'anthropic',
    model: 'claude-sonnet-4-5',
  });
  expect(parseModelRef('openrouter:anthropic/claude-sonnet-4.5')).toEqual({
    provider: 'openrouter',
    model: 'anthropic/claude-sonnet-4.5',
  });
  expect(parseModelRef('gpt-5-mini')).toEqual({
    provider: 'openai',
    model: 'gpt-5-mini',
  });
  expect(() => parseModelRef('not a model')).toThrow();
});

it('resolves a Dot model before the default and requires its provider key', () => {
  const config = {
    ...base,
    apiKey: 'openai',
    defaultModel: 'openai:gpt-5.2',
  };
  expect(resolveModel(config)).toEqual({
    provider: 'openai',
    model: 'gpt-5.2',
  });
  expect(() => resolveModel(config, 'anthropic:claude-haiku-4-5')).toThrow(
    /ANTHROPIC_API_KEY/,
  );
  expect(
    resolveModel(
      { ...config, anthropicKey: 'anthropic' },
      'anthropic:claude-haiku-4-5',
    ),
  ).toEqual({ provider: 'anthropic', model: 'claude-haiku-4-5' });
  expect(() => resolveModel({ ...base })).toThrow(/DEFAULT_MODEL/);
});

it('keeps the legacy OPENAI_MODEL default working', () => {
  const config = { ...base, apiKey: 'openai', model: 'gpt-5-mini' };
  expect(resolveModel(config)).toEqual({
    provider: 'openai',
    model: 'gpt-5-mini',
  });
  expect(setupStatus(config)).toMatchObject({
    missing: [],
    model: true,
    defaultModel: 'openai:gpt-5-mini',
  });
});

it('reports the missing key for the configured default provider', () => {
  expect(
    setupStatus({ ...base, defaultModel: 'anthropic:claude-sonnet-4-5' })
      .missing,
  ).toEqual(['ANTHROPIC_API_KEY']);
  expect(setupStatus({ ...base }).missing).toEqual(['DEFAULT_MODEL']);
  expect(
    setupStatus({
      ...base,
      openrouterKey: 'router',
      defaultModel: 'openrouter:openai/gpt-5-mini',
      workerModel: 'openrouter:anthropic/claude-haiku-4.5',
    }),
  ).toMatchObject({
    missing: [],
    providers: ['openrouter'],
    workerModel: 'openrouter:anthropic/claude-haiku-4.5',
  });
});

it('builds adapters with provider-appropriate token options', () => {
  const config = {
    ...base,
    apiKey: 'openai',
    anthropicKey: 'anthropic',
    openrouterKey: 'router',
  };
  expect(configuredProviders(config)).toEqual([
    'openai',
    'anthropic',
    'openrouter',
  ]);
  expect(
    textAdapter(config, { provider: 'openai', model: 'gpt-5.2' }, 100)
      .modelOptions,
  ).toEqual({ max_completion_tokens: 100 });
  expect(
    textAdapter(config, { provider: 'anthropic', model: 'claude-x' }, 100)
      .modelOptions,
  ).toEqual({ max_tokens: 100 });
  expect(
    textAdapter(config, { provider: 'openrouter', model: 'a/b' }, 100)
      .modelOptions,
  ).toEqual({ max_tokens: 100 });
});
