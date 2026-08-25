/**
 * Daily Google Ads export for MarketingDailyBot.
 *
 * Setup:
 * 1. Replace SPREADSHEET_URL with the Budget Pacing spreadsheet URL.
 * 2. Add this script in Google Ads under Tools > Scripts.
 * 3. Authorize it, run Preview once, and schedule it daily after 3:00 AM
 *    in the Google Ads account timezone.
 *
 * Each run refreshes the last 10 completed days (ending yesterday). Existing
 * rows for those dates are replaced so late conversion/revenue updates from
 * Google Ads are reflected without creating duplicates.
 */
const CONFIG = {
  SPREADSHEET_URL: 'https://docs.google.com/spreadsheets/d/1SBRI8qw2ve-iwxejowFxfxLs5uZX44Ab7-i5QSv5pDs/edit',
  RAW_TAB_NAME: 'Google Ads Raw',
  LOOKBACK_DAYS: 10,
};

const CHANNEL_NAMES = {
  PERFORMANCE_MAX: 'Performance Max',
  SEARCH: 'Search',
  SHOPPING: 'Shopping',
  VIDEO: 'Video',
  DEMAND_GEN: 'Demand Gen',
};

function main() {
  const account = AdsApp.currentAccount();
  const timezone = account.getTimeZone();
  const dateRange = getCompletedDateRange(timezone, CONFIG.LOOKBACK_DAYS);

  const query = `
    SELECT
      segments.date,
      campaign.advertising_channel_type,
      metrics.cost_micros,
      metrics.conversions_value
    FROM campaign
    WHERE segments.date BETWEEN '${dateRange.start}' AND '${dateRange.end}'
      AND campaign.status != 'REMOVED'
  `;

  const totals = {};
  for (const row of AdsApp.search(query, {apiVersion: 'v25'})) {
    const reportDate = row.segments.date;
    const apiType = row.campaign.advertisingChannelType;
    const campaignType = CHANNEL_NAMES[apiType];
    if (!campaignType) {
      console.log(`Skipping unsupported channel type: ${apiType}`);
      continue;
    }
    const key = `${reportDate}|${campaignType}`;
    if (!totals[key]) {
      totals[key] = {spend: 0, revenue: 0};
    }
    totals[key].spend += Number(row.metrics.costMicros || 0) / 1000000;
    totals[key].revenue += Number(row.metrics.conversionsValue || 0);
  }

  const updatedAt = Utilities.formatDate(new Date(), timezone, "yyyy-MM-dd'T'HH:mm:ssXXX");
  const outputRows = [];
  const refreshedDates = getIsoDates(dateRange.start, dateRange.end);
  for (const reportDate of refreshedDates) {
    for (const campaignType of Object.values(CHANNEL_NAMES).sort()) {
      const values = totals[`${reportDate}|${campaignType}`] || {spend: 0, revenue: 0};
      outputRows.push([
        reportDate,
        campaignType,
        values.spend,
        values.revenue,
        values.spend === 0 ? 0 : values.revenue / values.spend,
        updatedAt,
      ]);
    }
  }

  const spreadsheet = SpreadsheetApp.openByUrl(CONFIG.SPREADSHEET_URL);
  let sheet = spreadsheet.getSheetByName(CONFIG.RAW_TAB_NAME);
  if (!sheet) {
    sheet = spreadsheet.insertSheet(CONFIG.RAW_TAB_NAME);
  }

  const header = ['Date', 'Campaign Type', 'Spend', 'Revenue', 'ROAS', 'Updated At'];
  const existing = sheet.getDataRange().getValues();
  const refreshedDateSet = new Set(refreshedDates);
  const retained = existing.length > 1
    ? existing.slice(1).filter(
        (row) => !refreshedDateSet.has(normalizeSheetDate(row[0], timezone))
      )
    : [];
  const allRows = [header].concat(retained, outputRows);

  sheet.clearContents();
  // Force ISO dates to remain text instead of Google Sheets date serials.
  sheet.getRange(1, 1, Math.max(allRows.length, 2), 1).setNumberFormat('@');
  sheet.getRange(1, 1, allRows.length, header.length).setValues(allRows);
  sheet.setFrozenRows(1);
  sheet.getRange(1, 1, 1, header.length)
    .setFontWeight('bold')
    .setBackground('#d9eaf7');
  if (allRows.length > 1) {
    sheet.getRange(2, 3, allRows.length - 1, 2).setNumberFormat('$#,##0.00');
    sheet.getRange(2, 5, allRows.length - 1, 1).setNumberFormat('0.00');
  }
  sheet.autoResizeColumns(1, header.length);
  console.log(
    `Refreshed ${outputRows.length} rows for ${dateRange.start} through ${dateRange.end}.`
  );
}

function getCompletedDateRange(timezone, lookbackDays) {
  if (!Number.isInteger(lookbackDays) || lookbackDays < 1) {
    throw new Error('LOOKBACK_DAYS must be a positive integer.');
  }

  const yesterday = Utilities.formatDate(
    new Date(Date.now() - 24 * 60 * 60 * 1000),
    timezone,
    'yyyy-MM-dd'
  );
  const start = shiftIsoDate(yesterday, -(lookbackDays - 1));
  return {start: start, end: yesterday};
}

function getIsoDates(start, end) {
  const dates = [];
  let current = start;
  while (current <= end) {
    dates.push(current);
    current = shiftIsoDate(current, 1);
  }
  return dates;
}

function shiftIsoDate(isoDate, days) {
  const value = new Date(`${isoDate}T12:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return Utilities.formatDate(value, 'UTC', 'yyyy-MM-dd');
}

function normalizeSheetDate(value, timezone) {
  if (value instanceof Date) {
    return Utilities.formatDate(value, timezone, 'yyyy-MM-dd');
  }
  return String(value || '').trim();
}
