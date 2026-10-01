import { CronExpressionParser } from 'cron-parser';
import { z } from 'zod';

export function validTimezone(value: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: value });
    return true;
  } catch {
    return false;
  }
}

export const cronSchema = z
  .string()
  .trim()
  .max(120)
  .refine(
    (value) => value.split(/\s+/).length === 5,
    'Use a five-field cron expression: minute hour day month weekday.',
  );
export const timezoneSchema = z
  .string()
  .trim()
  .min(1)
  .max(64)
  .refine(validTimezone, 'Use an IANA time zone such as Asia/Kolkata.');

/** Next run time (ms) strictly after `from`, evaluated in `timezone`. */
export function nextCronRun(
  cron: string,
  timezone: string | null | undefined,
  from = Date.now(),
): number {
  const parsed = cronSchema.safeParse(cron);
  if (!parsed.success) throw new Error(parsed.error.issues[0].message);
  const tz = timezone || 'UTC';
  if (!validTimezone(tz)) throw new Error('Unknown time zone.');
  try {
    return CronExpressionParser.parse(parsed.data, {
      currentDate: new Date(from),
      tz,
    })
      .next()
      .getTime();
  } catch (error) {
    throw new Error(
      `Invalid cron expression: ${error instanceof Error ? error.message : 'unreadable'}.`,
      { cause: error },
    );
  }
}

export function describeNextRun(at: number, timezone: string | null) {
  return new Intl.DateTimeFormat('en-GB', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: timezone || 'UTC',
  }).format(at);
}
