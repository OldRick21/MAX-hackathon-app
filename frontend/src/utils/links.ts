// Ссылка на онлайн-занятие. В контракте расписания нет отдельного поля для ссылки,
// поэтому берём первый https-адрес из описания или места проведения.
export function findMeetingLink(...texts: (string | null | undefined)[]): string | null {
  for (const t of texts) {
    const m = t?.match(/https:\/\/[^\s<>"']+/i);
    if (m) return m[0].replace(/[).,;]+$/, '');
  }
  return null;
}

export function formatBytes(n: number) {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} КБ`;
  return `${(n / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`;
}

export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

export function plural(n: number, one: string, few: string, many: string) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}
