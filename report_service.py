"""CSV normalization, metric calculation, and report formatting."""

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


CAMPAIGN_TYPE_NAMES: Dict[str, str] = {
    "Search": "Google Search",
    "Shopping": "Shopping",
    "Video": "Google Video",
    "Demand Gen": "Google DG",
    "Performance Max": "Pmax",
}


@dataclass(frozen=True)
class ChannelMetrics:
    """Normalized performance metrics for one advertising channel."""

    name: str
    spend: Decimal
    revenue: Decimal
    add_to_cart: int = 0

    @property
    def roas(self) -> Decimal:
        return calculate_roas(self.revenue, self.spend)


def calculate_roas(revenue: Decimal, spend: Decimal) -> Decimal:
    """Calculate ROAS safely, returning zero when spend is zero."""
    if spend == 0:
        return Decimal("0")
    return revenue / spend


def _read_google_ads_csv(csv_path: Path) -> Tuple[date, List[ChannelMetrics]]:
    """Read a Google Ads CSV that may contain report metadata above its header."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        lines = csv_file.readlines()

    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip().startswith("Day,Campaign type,")
        ),
        None,
    )
    if header_index is None:
        raise ValueError(f"Could not find a Google Ads header in {csv_path.name}.")

    rows = csv.DictReader(lines[header_index:])
    report_date = None
    metrics: List[ChannelMetrics] = []

    for row in rows:
        if not row.get("Day") or not row.get("Campaign type"):
            continue

        row_date = datetime.strptime(row["Day"].strip(), "%Y-%m-%d").date()
        report_date = report_date or row_date
        if row_date != report_date:
            raise ValueError(f"{csv_path.name} contains more than one report date.")

        spend = Decimal(row["Cost"].replace(",", "").strip())
        roas = Decimal(row["ROAS"].replace(",", "").strip() or "0")
        campaign_type = row["Campaign type"].strip()
        metrics.append(
            ChannelMetrics(
                name=CAMPAIGN_TYPE_NAMES.get(campaign_type, campaign_type),
                spend=spend,
                revenue=spend * roas,
            )
        )

    if report_date is None or not metrics:
        raise ValueError(f"No advertising rows were found in {csv_path.name}.")

    return report_date, metrics


def _date_from_filename(csv_path: Path) -> date:
    """Extract the last ISO date from a provider-generated filename."""
    matches = re.findall(r"\d{4}-\d{2}-\d{2}", csv_path.name)
    if not matches:
        raise ValueError(f"Could not determine a report date from {csv_path.name}.")
    return datetime.strptime(matches[-1], "%Y-%m-%d").date()


def _read_reddit_ads_csv(csv_path: Path) -> Tuple[date, List[ChannelMetrics]]:
    """Aggregate Reddit ad-level spend and purchase ROAS into one channel row."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        rows = csv.DictReader(csv_file)
        fieldnames = rows.fieldnames or []
        spend_column = "Amount Spent (USD)"
        roas_column = "Purchase ROAS (Return on Ad Spend)"
        if spend_column not in fieldnames or roas_column not in fieldnames:
            raise ValueError(f"Could not find Reddit Ads columns in {csv_path.name}.")

        total_spend = Decimal("0")
        total_revenue = Decimal("0")
        for row in rows:
            spend_value = (row.get(spend_column) or "").replace(",", "").strip()
            if not spend_value:
                continue
            spend = Decimal(spend_value)
            roas = Decimal(
                (row.get(roas_column) or "0").replace(",", "").strip() or "0"
            )
            total_spend += spend
            total_revenue += spend * roas

    return _date_from_filename(csv_path), [
        ChannelMetrics(name="Reddit", spend=total_spend, revenue=total_revenue)
    ]


def read_raw_csv(csv_path: Path) -> Tuple[date, List[ChannelMetrics]]:
    """Detect the provider from CSV headers and parse it without renaming."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        preview = "".join(csv_file.readlines()[:5])

    if "Day,Campaign type,Currency code,Cost,ROAS" in preview:
        return _read_google_ads_csv(csv_path)
    if "Amount Spent (USD)" in preview and "Purchase ROAS" in preview:
        return _read_reddit_ads_csv(csv_path)
    raise ValueError(f"Unsupported raw CSV format: {csv_path.name}")


def process_csv_files(
    csv_paths: Iterable[Path],
) -> Tuple[date, List[ChannelMetrics]]:
    """Convert Google Ads CSV files into a date and normalized channel metrics."""
    paths = list(csv_paths)
    if not paths:
        raise ValueError("No CSV files were provided for report processing.")

    report_date = None
    all_metrics: List[ChannelMetrics] = []
    for csv_path in paths:
        file_date, metrics = read_raw_csv(csv_path)
        report_date = report_date or file_date
        if file_date != report_date:
            raise ValueError("All CSV files must belong to the same report date.")
        all_metrics.extend(metrics)

    return report_date, all_metrics


def _aggregate_metrics(metrics: Iterable[ChannelMetrics]) -> List[ChannelMetrics]:
    """Combine rows that belong to the same advertising channel."""
    totals: Dict[str, Dict[str, object]] = {}
    for row in metrics:
        channel = totals.setdefault(
            row.name,
            {"spend": Decimal("0"), "revenue": Decimal("0"), "add_to_cart": 0},
        )
        channel["spend"] += row.spend
        channel["revenue"] += row.revenue
        channel["add_to_cart"] += row.add_to_cart

    return [
        ChannelMetrics(
            name=name,
            spend=values["spend"],
            revenue=values["revenue"],
            add_to_cart=values["add_to_cart"],
        )
        for name, values in totals.items()
    ]


def save_processed_csv(
    output_path: Path,
    report_date: date,
    metrics: Iterable[ChannelMetrics],
) -> None:
    """Save one daily summary row as clean tabular data without Slack markup."""
    rows = _aggregate_metrics(metrics)
    total_spend = sum((row.spend for row in rows), Decimal("0"))
    total_revenue = sum((row.revenue for row in rows), Decimal("0"))

    record = {
        "Date": report_date.isoformat(),
        "Total Spend": f"{total_spend:.2f}",
        "Total Revenue": f"{total_revenue:.2f}",
        "Total ROAS": f"{calculate_roas(total_revenue, total_spend):.2f}",
    }
    for row in rows:
        record[f"{row.name} Spend"] = f"{row.spend:.2f}"
        record[f"{row.name} ROAS"] = f"{row.roas:.2f}"
        # Reddit reporting only needs Spend and ROAS in the processed output.
        if row.name != "Reddit":
            record[f"{row.name} Revenue"] = f"{row.revenue:.2f}"
            record[f"{row.name} ATC"] = str(row.add_to_cart)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(record))
        writer.writeheader()
        writer.writerow(record)


def load_processed_csv(csv_path: Path) -> Tuple[date, List[ChannelMetrics]]:
    """Load a processed daily summary for Slack formatting or later analysis."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        record = next(csv.DictReader(csv_file), None)
    if not record or not record.get("Date"):
        raise ValueError(f"Processed report {csv_path.name} is empty or invalid.")

    report_date = datetime.strptime(record["Date"], "%Y-%m-%d").date()
    channel_names = [
        column[: -len(" Spend")]
        for column in record
        if column.endswith(" Spend") and column != "Total Spend"
    ]
    metrics = []
    for name in channel_names:
        spend = Decimal(record[f"{name} Spend"] or "0")
        if name == "Reddit" and f"{name} Revenue" not in record:
            roas = Decimal(record.get(f"{name} ROAS", "0") or "0")
            revenue = spend * roas
        else:
            revenue = Decimal(record.get(f"{name} Revenue", "0") or "0")
        metrics.append(
            ChannelMetrics(
                name=name,
                spend=spend,
                revenue=revenue,
                add_to_cart=int(record.get(f"{name} ATC", "0") or "0"),
            )
        )
    return report_date, metrics


def format_slack_report(
    report_date: date,
    metrics: Iterable[ChannelMetrics],
) -> str:
    """Format normalized metrics as a Slack-friendly daily report."""
    rows = _aggregate_metrics(metrics)
    total_spend = sum((row.spend for row in rows), Decimal("0"))
    total_revenue = sum((row.revenue for row in rows), Decimal("0"))
    total_roas = calculate_roas(total_revenue, total_spend)

    lines = [
        f"📊 *Bluevua Daily Report {report_date:%m/%d}*",
        (
            f"*Total Spend:* ${total_spend:,.2f} | "
            f"*Total Revenue:* ${total_revenue:,.2f} | "
            f"*ROAS:* {total_roas:.2f}"
        ),
        "--------------------------------------------------",
    ]

    for row in rows:
        line = f"• *{row.name} Spend:* ${row.spend:,.2f} | *ROAS:* {row.roas:.2f}"
        if row.add_to_cart:
            line += f" | *ATC:* {row.add_to_cart}"
        lines.append(line)

    return "\n".join(lines)
