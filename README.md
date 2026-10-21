# OrderFlow — D2C Order Follow-up Automation

OrderFlow is a working Django prototype that turns order data into an accountable follow-up queue. It validates CSV/XLSX imports, evaluates five business rules, assigns tasks, tracks every manager action and exports a daily exception report.

The interface uses Django templates and a locally compiled Tailwind CSS bundle. Data is persisted in SQLite.

## What is included

- CSV and Excel import with required-column, missing-value, duplicate-ID, invalid-date, status and payment validation.
- A data-correction queue. Invalid rows are retained for review and excluded from rule evaluation.
- Five automatic exception rules:
  - Unconfirmed for more than 2 hours.
  - Confirmed but not dispatched within 24 hours.
  - Dispatched and past the expected delivery date.
  - Failed delivery attempt.
  - High-value (over ₹5,000) and overdue.
- Deduplicated exception tasks, uniquely keyed by order and rule category.
- Manager workflow for task reassignment and status updates.
- Dashboard filters for date, exception type, employee and priority.
- Daily CSV report for new, unresolved, overdue and high-priority exceptions.
- Persistent audit trail for creation, status changes, reassignment, auto-resolution and rule edits.
- Editable business thresholds, including the live-interview 24-to-12-hour dispatch rule scenario.
- 120 seeded orders plus a 124-row sample import containing invalid dates, a missing ID and duplicate IDs.

## Quick start

Requires Python 3.12+ and Node.js 18+ (Node is only needed when rebuilding Tailwind CSS).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo --reset
python manage.py runserver
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) and sign in with:

```text
Username: manager
Password: manager123
```

The current workspace already contains a seeded SQLite database. Run `seed_demo --reset` whenever a clean demonstration state is needed.

## Demonstrating the assignment

1. Open **Overview** to show active exception counts, rule-category load, employee workload and the urgent queue.
2. Open **Data imports** and upload `static/sample/sample_orders.csv`. The valid records update safely; the invalid and duplicate rows appear in the correction queue.
3. Open a follow-up task, change its owner and status, then show the new entries in its history and the global **Audit trail**.
4. Open **Rule engine**, change the dispatch threshold from 24 to 12 hours and save. All orders are re-evaluated without generating duplicate tasks.
5. Open **Daily report** and download the CSV.

## Import schema

The file must contain these headers (spaces and title case are also accepted):

```text
order_id, order_date, customer_name, phone_number, order_value,
payment_type, order_status, dispatch_date, expected_delivery_date,
assigned_employee
```

Accepted payment types are `prepaid` and `cod`. Supported statuses are `unconfirmed`, `confirmed`, `dispatched`, `out_for_delivery`, `failed_delivery`, `delivered` and `cancelled`.

## Rule and duplicate-task design

`operations/services.py` holds the import validator and rule engine. Every task has a database-level unique constraint across `(order, category)`. Rule evaluation uses `update_or_create`, so re-uploading a record or rerunning rules updates the existing exception instead of creating a duplicate. A task is automatically resolved when its order no longer matches the rule.

Thresholds live in the `RuleSetting` table and are editable from the UI. That makes a business change such as 24-hour to 12-hour dispatch escalation an audited configuration change rather than a code edit.

## Tests and CSS build

```bash
python manage.py check
python manage.py test
npx tailwindcss -i ./static/src/input.css -o ./static/css/app.css --minify
```

Tests cover task deduplication, high-value overdue behavior, configurable thresholds, import error isolation, reassignment audit history and report download.

## Business impact

- **Less manual work:** employees receive a pre-prioritised queue instead of reconciling spreadsheets and WhatsApp messages.
- **Fewer forgotten orders:** every exception remains open with an owner and SLA until resolved or the source state changes.
- **Useful effectiveness metrics:** exception ageing, overdue rate, median resolution time, delivery-success rate after follow-up, exceptions per 100 orders and workload distribution.
- **Time-saved estimate:** compare the previous daily time spent scanning orders and coordinating owners against time spent resolving the generated queue. For example, 120 orders at 45 seconds of manual review is 90 minutes before coordination; the rule engine reduces the review stage to seconds.
- **Current limitations:** no courier or commerce-platform integration, no automated messaging, one local database, simple role model, no background scheduler and no production deployment/security hardening.

## Project layout

```text
operations/          Models, views, forms, rule engine, tests and seed command
orderflow/           Django project settings and routes
templates/           Django templates
static/src/          Tailwind source
static/css/          Compiled local CSS
static/sample/       Demonstration import dataset
db.sqlite3           Local persisted demo database
```
