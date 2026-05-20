import requests
import os
from datetime import datetime, timedelta

JIRA_URL = "https://hackerearth.atlassian.net"
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]


# ── Date range ───────────────────────────────────────────────────────────────

def get_last_week_range():
    today = datetime.now()
    start = today - timedelta(days=6)
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


# ── JIRA helpers ─────────────────────────────────────────────────────────────

def get_custom_field_ids():
    """Discover IDs for the custom fields we care about."""
    url = f"{JIRA_URL}/rest/api/3/field"
    resp = requests.get(
        url,
        auth=(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()

    target_fields = {
        "content issue status":       "content_issue_status",
        "# of customers impacted":    "customers_impacted",
        "# of test slugs impacted":   "test_slugs_impacted",
        "# of candidates impacted":   "candidates_impacted",
        "setter":                     "setter",
    }

    field_map = {}
    for field in resp.json():
        name_lower = field["name"].lower().strip()
        for target, key in target_fields.items():
            if target in name_lower:
                field_map[key] = field["id"]
                break

    print(f"Discovered custom fields: {field_map}")
    return field_map


def fetch_issues(start_date, end_date, fields):
    jql = (
        'project = "TCE" '
        'AND type = "Content Requests" '
        'AND "request type[dropdown]" = "Content Issue" '
        f'AND created >= "{start_date}" '
        f'AND created <= "{end_date}" '
        'ORDER BY created DESC'
    )

    field_ids = list(fields.values()) + ["summary", "assignee"]

    url = f"{JIRA_URL}/rest/api/3/search/jql"

    resp = requests.post(
        url,
        auth=(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json={"jql": jql, "maxResults": 100, "fields": field_ids},
    )
    resp.raise_for_status()
    issues = resp.json()["issues"]
    print(f"Fetched {len(issues)} issues for {start_date} → {end_date}")
    return issues


# ── Processing ────────────────────────────────────────────────────────────────

def get_field_value(issue, field_id):
    if not field_id:
        return None
    return issue["fields"].get(field_id)


def parse_status(raw):
    """Normalise Content Issue Status to 'valid', 'invalid', or 'customer'."""
    if isinstance(raw, dict):
        text = raw.get("value", "")
    elif isinstance(raw, str):
        text = raw
    else:
        return "unknown"
    text = text.lower().strip()
    if "invalid" in text:
        return "invalid"
    if "valid" in text:
        return "valid"
    if "customer" in text:
        return "customer"
    return "unknown"


def to_int(val):
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str) and val.strip().isdigit():
        return int(val.strip())
    return 0


def process_issues(issues, fields):
    valid, invalid_count, customer_count = [], 0, 0
    status_field = fields.get("content_issue_status")

    for issue in issues:
        status = parse_status(get_field_value(issue, status_field))
        if status == "valid":
            valid.append(issue)
        elif status == "invalid":
            invalid_count += 1
        elif status == "customer":
            customer_count += 1

    return {
        "total":          len(issues),
        "valid":          valid,
        "valid_count":    len(valid),
        "invalid_count":  invalid_count,
        "customer_count": customer_count,
    }


def aggregate_impact(valid_issues, fields):
    total_tests = total_candidates = total_customers = 0
    setter_counts = {}

    for issue in valid_issues:
        total_tests      += to_int(get_field_value(issue, fields.get("test_slugs_impacted")))
        total_candidates += to_int(get_field_value(issue, fields.get("candidates_impacted")))
        total_customers  += to_int(get_field_value(issue, fields.get("customers_impacted")))

        setter_raw = get_field_value(issue, fields.get("setter"))
        if isinstance(setter_raw, dict):
            name = setter_raw.get("displayName") or setter_raw.get("name")
        elif isinstance(setter_raw, str):
            name = setter_raw
        else:
            name = None

        if name:
            setter_counts[name] = setter_counts.get(name, 0) + 1

    return total_tests, total_candidates, total_customers, setter_counts


# ── Slack message ─────────────────────────────────────────────────────────────

def build_slack_message(stats, impact, start_date, end_date):
    total_tests, total_candidates, total_customers, setter_counts = impact

    if setter_counts:
        setter_lines = "\n".join(
            f"  • {name}: {count}" for name, count in sorted(setter_counts.items())
        )
    else:
        setter_lines = "  NA"

    return (
        f"@channel *Weekly Content Issue Summary (Assessment)*\n"
        f"*Period*: {start_date}  →  {end_date}\n\n"
        f"*Overall Stats*\n"
        f"  • Total issues reported: {stats['total']}\n"
        f"  • Total valid issues: {stats['valid_count']}\n"
        f"  • Total invalid issues: {stats['invalid_count']}\n"
        f"  • Customer-content issues: {stats['customer_count']}\n\n"
        f"*Valid Issues Per Member (Setter)*\n"
        f"{setter_lines}\n\n"
        f"*Impact*\n"
        f"  • # of tests impacted: {total_tests}\n"
        f"  • # of candidates impacted: {total_candidates}\n"
        f"  • # of customers impacted: {total_customers}"
    )


def post_to_slack(message):
    resp = requests.post(SLACK_WEBHOOK_URL, json={"text": message})
    resp.raise_for_status()
    print("✅ Posted to Slack successfully.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    start_date, end_date = get_last_week_range()
    fields   = get_custom_field_ids()
    issues   = fetch_issues(start_date, end_date, fields)
    stats    = process_issues(issues, fields)
    impact   = aggregate_impact(stats["valid"], fields)
    message  = build_slack_message(stats, impact, start_date, end_date)

    print("\n── Message preview ──────────────────────────")
    print(message)
    print("─────────────────────────────────────────────\n")

    post_to_slack(message)


if __name__ == "__main__":
    main()
