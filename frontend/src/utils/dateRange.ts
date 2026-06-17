export function formatDate(date: Date): string {
  return date.toISOString().split('T')[0];
}

export function getTodayDate(): string {
  return formatDate(new Date());
}

export function getDateYearsAgo(years: number): string {
  const date = new Date();
  date.setFullYear(date.getFullYear() - years);
  return formatDate(date);
}

export function getDefaultOneYearRange(): { startDate: string; endDate: string } {
  return {
    startDate: getDateYearsAgo(1),
    endDate: getTodayDate(),
  };
}
