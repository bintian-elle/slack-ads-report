"""CSV normalization, metric calculation, and report formatting."""

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple


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
    add_to_cart: Decimal = Decimal("0")

    @property
    def roas(self) -> Decimal:
        return calculate_roas(self.revenue, self.spend)


def calculate_roas(revenue: Decimal, spend: Decimal) -> Decimal:
    """Calculate ROAS safely, returning zero when spend is zero."""
    if spend == 0:
        return Decimal("0")
    return revenue / spend


def _aggregate_metrics(metrics: Iterable[ChannelMetrics]) -> List[ChannelMetrics]:
    """Combine rows that belong to the same advertising channel."""
    totals: Dict[str, Dict[str, object]] = {}
    for row in metrics:
        channel = totals.setdefault(
            row.name,
            {
                "spend": Decimal("0"),
                "revenue": Decimal("0"),
                "add_to_cart": Decimal("0"),
            },
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


def cleanup_processed_reports(
    processed_dir: Path,
    retention_days: int = 7,
) -> List[Path]:
    """Keep daily processed CSVs for the latest retention window."""
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1.")

    dated_paths = []
    for path in processed_dir.glob("daily_report_*.csv"):
        match = re.fullmatch(r"daily_report_(\d{4}-\d{2}-\d{2})\.csv", path.name)
        if not match:
            continue
        try:
            report_date = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        dated_paths.append((report_date, path))
    if not dated_paths:
        return []

    newest_date = max(report_date for report_date, _ in dated_paths)
    cutoff = newest_date - timedelta(days=retention_days - 1)
    removed = []
    for report_date, path in dated_paths:
        if report_date < cutoff:
            path.unlink()
            removed.append(path)
    return removed


def save_processed_csv(
    output_path: Path,
    report_date: date,
    metrics: Iterable[ChannelMetrics],
) -> None:
    """Save one daily summary row as clean tabular data without Slack markup."""
    rows = _aggregate_metrics(metrics)
    special_rows = {
        row.name: row
        for row in rows
        if row.name in {"Shopify", "RO system"}
    }
    rows = [row for row in rows if row.name not in special_rows]

    # The daily report presents Video and Demand Gen as one Google DG line.
    google_dg_rows = [
        row for row in rows if row.name in {"Google Video", "Google DG"}
    ]
    if google_dg_rows:
        rows = [
            row for row in rows if row.name not in {"Google Video", "Google DG"}
        ]
        rows.append(
            ChannelMetrics(
                name="Google DG",
                spend=sum((row.spend for row in google_dg_rows), Decimal("0")),
                revenue=sum((row.revenue for row in google_dg_rows), Decimal("0")),
            )
        )
    total_spend = sum((row.spend for row in rows), Decimal("0"))
    total_revenue = (
        special_rows["Shopify"].revenue
        if "Shopify" in special_rows
        else sum((row.revenue for row in rows), Decimal("0"))
    )

    record = {
        "Date": report_date.isoformat(),
        "Total Spend": f"{total_spend:.2f}",
        "Total Revenue": f"{total_revenue:.2f}",
        "Total ROAS": f"{calculate_roas(total_revenue, total_spend):.2f}",
    }
    if "Shopify" in special_rows:
        record["Shopify Total Revenue"] = f"{special_rows['Shopify'].revenue:.2f}"
    if "RO system" in special_rows:
        record["RO system Revenue"] = f"{special_rows['RO system'].revenue:.2f}"
    row_by_name = {row.name: row for row in rows}
    preferred_order = (
        "Pmax",
        "Google Search",
        "Shopping",
        "Meta",
        "Bing",
        "Engagement",
        "Google DG",
        "TikTok",
        "Reddit",
    )
    ordered_rows = [
        row_by_name[name] for name in preferred_order if name in row_by_name
    ]
    ordered_rows.extend(row for row in rows if row.name not in preferred_order)

    for row in ordered_rows:
        record[f"{row.name} Spend"] = f"{row.spend:.2f}"
        if row.name != "Engagement":
            record[f"{row.name} ROAS"] = f"{row.roas:.2f}"
        if row.name == "Meta" and row.add_to_cart:
            record[f"{row.name} ATC"] = str(row.add_to_cart)

    google_rows = [
        row
        for row in rows
        if row.name in {"Pmax", "Google Search", "Shopping", "Google DG"}
    ]
    if google_rows:
        google_spend = sum((row.spend for row in google_rows), Decimal("0"))
        google_revenue = sum((row.revenue for row in google_rows), Decimal("0"))
        record["Google Ads Spend"] = f"{google_spend:.2f}"
        record["Google Ads ROAS"] = f"{calculate_roas(google_revenue, google_spend):.2f}"

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
        if column.endswith(" Spend")
        and column not in {"Total Spend", "Google Ads Spend"}
    ]
    metrics = []
    if record.get("Shopify Total Revenue"):
        metrics.append(
            ChannelMetrics(
                name="Shopify",
                spend=Decimal("0"),
                revenue=Decimal(record["Shopify Total Revenue"]),
            )
        )
    if record.get("RO system Revenue"):
        metrics.append(
            ChannelMetrics(
                name="RO system",
                spend=Decimal("0"),
                revenue=Decimal(record["RO system Revenue"]),
            )
        )
    for name in channel_names:
        spend = Decimal(record[f"{name} Spend"] or "0")
        if f"{name} Revenue" not in record:
            roas = Decimal(record.get(f"{name} ROAS", "0") or "0")
            revenue = spend * roas
        else:
            revenue = Decimal(record.get(f"{name} Revenue", "0") or "0")
        metrics.append(
            ChannelMetrics(
                name=name,
                spend=spend,
                revenue=revenue,
                add_to_cart=Decimal(record.get(f"{name} ATC", "0") or "0"),
            )
        )
    return report_date, metrics


def format_slack_report(
    report_date: date,
    metrics: Iterable[ChannelMetrics],
    mtd_summary: Optional[Mapping[str, str]] = None,
) -> str:
    """Format normalized metrics using the fixed Slack daily report template."""
    rows = _aggregate_metrics(metrics)
    row_by_name = {row.name: row for row in rows}

    dg_rows = [
        row_by_name[name]
        for name in ("Google Video", "Google DG")
        if name in row_by_name
    ]
    if dg_rows:
        dg_spend = sum((row.spend for row in dg_rows), Decimal("0"))
        dg_revenue = sum((row.revenue for row in dg_rows), Decimal("0"))
        row_by_name["Google DG"] = ChannelMetrics(
            name="Google DG",
            spend=dg_spend,
            revenue=dg_revenue,
        )

    def currency(value: Decimal) -> str:
        return f"${value:,.2f}"

    def spend_roas_line(label: str, channel_name: str) -> str:
        row = row_by_name.get(channel_name)
        if row is None:
            return f"• *{label} Spend:* - | *ROAS:* -"
        return (
            f"• *{label} Spend:* {currency(row.spend)} | "
            f"*ROAS:* {row.roas:.2f}"
        )

    required_spend_channels = {
        "Pmax",
        "Google Search",
        "Shopping",
        "Meta",
        "Bing",
        "Engagement",
        "Google DG",
        "Reddit",
    }
    has_complete_spend = required_spend_channels.issubset(row_by_name)
    if has_complete_spend:
        total_spend_channels = set(required_spend_channels)
        if "TikTok" in row_by_name:
            total_spend_channels.add("TikTok")
        total_spend = (
            sum(
                (row_by_name[name].spend for name in total_spend_channels),
                Decimal("0"),
            )
        )
        total_spend_text = currency(total_spend)
    else:
        total_spend_text = "-"

    shopify = row_by_name.get("Shopify")
    total_revenue_text = currency(shopify.revenue) if shopify else "-"
    total_roas_text = (
        f"{calculate_roas(shopify.revenue, total_spend):.2f}"
        if shopify and has_complete_spend
        else "-"
    )
    ro_system = row_by_name.get("RO system")
    ro_system_text = currency(ro_system.revenue) if ro_system else "-"

    meta = row_by_name.get("Meta")
    if meta is None:
        meta_line = "• *Meta Spend:* - | *ROAS:* - | *ATC:* -"
    else:
        meta_line = (
            f"• *Meta Spend:* {currency(meta.spend)} | *ROAS:* {meta.roas:.2f} | "
            f"*ATC:* ${meta.add_to_cart:,.2f}"
        )

    engagement = row_by_name.get("Engagement")
    engagement_text = currency(engagement.spend) if engagement else "-"

    reddit = row_by_name.get("Reddit")
    if reddit is None:
        reddit_line = "• *Reddit Spend:* - | *Reddit ROAS:* -"
    else:
        reddit_line = (
            f"• *Reddit Spend:* {currency(reddit.spend)} | "
            f"*Reddit ROAS:* {reddit.roas:.2f}"
        )

    google_rows = [
        row_by_name[name]
        for name in (
            "Pmax",
            "Google Search",
            "Shopping",
            "Google Video",
            "Google DG",
        )
        if name in row_by_name
    ]
    # Avoid double counting Video after it has been combined into Google DG.
    if "Google Video" in row_by_name and "Google DG" in row_by_name:
        google_rows = [
            row
            for row in google_rows
            if row.name != "Google Video"
        ]
    if google_rows:
        google_spend = sum((row.spend for row in google_rows), Decimal("0"))
        google_revenue = sum((row.revenue for row in google_rows), Decimal("0"))
        google_line = (
            f"• *google ads spend:* {currency(google_spend)} | "
            f"*google ads ROAS:* "
            f"{calculate_roas(google_revenue, google_spend):.2f}"
        )
    else:
        google_line = "• *google ads spend:* - | *google ads ROAS:* -"

    lines = [
        f"📊 *Bluevua Daily Report {report_date:%m/%d}*",
        (
            f"*Total Spend:* {total_spend_text} | "
            f"*Total Revenue:* {total_revenue_text} | *ROAS:* {total_roas_text}"
        ),
        "--------------------------------------------------",
        f"• *RO system Revenue:* {ro_system_text}",
        spend_roas_line("Pmax", "Pmax"),
        spend_roas_line("Google Search", "Google Search"),
        spend_roas_line("Shopping", "Shopping"),
        meta_line,
        spend_roas_line("Bing", "Bing"),
        f"• *Engagement:* {engagement_text}",
        spend_roas_line("Google DG", "Google DG"),
        spend_roas_line("TikTok", "TikTok"),
        reddit_line,
        google_line,
    ]
    if mtd_summary is not None:
        lines.extend(
            [
                "--------------------------------------------------",
                f"• *MTD Paid Media Spend:* {mtd_summary['paid_media_spend']}",
                f"• *MTD Total Revenue:* {mtd_summary['total_revenue']}",
                f"• *MTD ROAS:* {mtd_summary['roas']}",
                (
                    "• *Avg Daily Budget Remaining:* "
                    f"{mtd_summary['avg_daily_budget_remaining']}"
                ),
                f"• *MTD Follower Growth:* {mtd_summary['follower_growth']}",
                f"• *MTD Spend Pacing %:* {mtd_summary['spend_pacing']}",
                (
                    "• *MTD Total Revenue Pacing %:* "
                    f"{mtd_summary['revenue_pacing']}"
                ),
            ]
        )
    return "\n".join(lines)
