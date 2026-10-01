const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

const pad = (value: string) => value.padStart(2, '0');

/** Plain-language reading of common five-field cron expressions. */
export function describeCron(cron: string): string {
  const [minute, hour, dom, month, dow] = cron.trim().split(/\s+/);
  if (!minute || !hour || !dom || !month || !dow) return cron;
  if (minute === '0' && hour === '*' && dom === '*' && month === '*' && dow === '*') return 'Every hour';
  const times = hour
    .split(',')
    .map((h) => (/^\d+$/.test(h) && /^\d+$/.test(minute) ? `${pad(h)}:${pad(minute)}` : null));
  if (times.some((t) => !t) || dom !== '*' || month !== '*') return cron;
  const at = times.join(', ');
  if (dow === '*') return `Every day at ${at}`;
  if (dow === '1-5') return `Weekdays at ${at}`;
  if (/^\d$/.test(dow)) return `${DAYS[Number(dow)]}s at ${at}`;
  return cron;
}

export const CRON_PRESETS = [
  { cron: '30 8 * * *', label: 'Every day at 08:30' },
  { cron: '0 9 * * 1-5', label: 'Weekdays at 09:00' },
  { cron: '0 9,13,17 * * 1-5', label: 'Weekdays at 09:00, 13:00, 17:00' },
  { cron: '0 9 * * 1', label: 'Mondays at 09:00' },
  { cron: '0 * * * *', label: 'Every hour' },
];

export const browserZone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
};
