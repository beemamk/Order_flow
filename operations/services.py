import csv
import io
import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from openpyxl import load_workbook

from .models import AuditLog, DataIssue, Employee, ExceptionTask, ImportBatch, Order, RuleSetting


REQUIRED_COLUMNS = {
    "order_id",
    "order_date",
    "customer_name",
    "phone_number",
    "order_value",
    "payment_type",
    "order_status",
    "dispatch_date",
    "expected_delivery_date",
    "assigned_employee",
}
ALIASES = {
    "order id": "order_id", "order date": "order_date", "customer name": "customer_name",
    "phone": "phone_number", "phone number": "phone_number", "order value": "order_value",
    "payment type": "payment_type", "status": "order_status", "order status": "order_status",
    "dispatch date": "dispatch_date", "expected delivery date": "expected_delivery_date",
    "assigned employee": "assigned_employee",
}
VALID_STATUSES = {key for key, _ in Order.STATUS_CHOICES}


def _normalise_header(value):
    raw = str(value or "").strip().lower().replace("-", " ").replace("_", " ")
    return ALIASES.get(raw, raw.replace(" ", "_"))


def _normalise_value(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def _unwrap_rtf(text):
    """If the file was created in macOS TextEdit in RTF mode, strip RTF formatting."""
    if not text.startswith("{\\rtf"):
        return text
    out = []
    i = 0
    n = len(text)
    skip_depth = 0
    destinations_to_skip = {"fonttbl", "colortbl", "stylesheet", "info", "*", "expandedcolortbl"}

    while i < n:
        char = text[i]
        if char == "{":
            match = re.match(r"^\{(\\\*|\\[a-zA-Z]+)", text[i:])
            if match:
                ctrl = match.group(1).lstrip("\\")
                if ctrl in destinations_to_skip or ctrl.startswith("*"):
                    skip_depth += 1
            i += 1
        elif char == "}":
            if skip_depth > 0:
                skip_depth -= 1
            i += 1
        elif skip_depth > 0:
            i += 1
        elif char == "\\":
            if i + 1 < n and text[i + 1] in r"\{}":
                out.append(text[i + 1])
                i += 2
            elif i + 1 < n and text[i + 1] in "\r\n":
                out.append("\n")
                i += 2
                if i < n and text[i] == "\n":
                    i += 1
            else:
                match = re.match(r"^\\([a-zA-Z]+)(-?\d*)? ?", text[i:])
                if match:
                    word = match.group(1)
                    if word in ("par", "line"):
                        out.append("\n")
                    i += len(match.group(0))
                else:
                    i += 1
        else:
            out.append(char)
            i += 1

    lines = [line.strip() for line in "".join(out).splitlines() if line.strip()]
    return "\n".join(lines)


def read_upload(upload):
    extension = upload.name.lower().rsplit(".", 1)[-1]
    if extension == "csv":
        upload.file.seek(0)
        raw_bytes = upload.file.read()
        upload.file.seek(0)

        # Handle multiple possible encodings
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw_bytes.decode("utf-8", errors="replace")

        # Automatically unwrap RTF if uploaded from macOS TextEdit
        text = _unwrap_rtf(text)

        # Strip leading and trailing empty lines
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return [], []

        # Sniff delimiter from non-empty lines
        sample = "\n".join(lines[:10])
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = "excel"

        string_io = io.StringIO("\n".join(lines))
        reader = csv.DictReader(string_io, dialect=dialect)
        headers = [_normalise_header(item) for item in (reader.fieldnames or [])]
        rows = []
        for raw in reader:
            if not any(v.strip() if isinstance(v, str) else v for v in raw.values() if v is not None):
                continue
            rows.append({_normalise_header(k): _normalise_value(v) for k, v in raw.items() if k is not None})
        return headers, rows

    # Excel format (.xlsx)
    workbook = load_workbook(upload, read_only=True, data_only=True)
    sheet = workbook.active
    values = sheet.iter_rows(values_only=True)
    raw_headers = None
    for row in values:
        if any(cell is not None and str(cell).strip() != "" for cell in row):
            raw_headers = row
            break
    if not raw_headers:
        return [], []
    headers = [_normalise_header(value) for value in raw_headers]
    rows = []
    for row in values:
        if not any(cell is not None and str(cell).strip() != "" for cell in row):
            continue
        rows.append(dict(zip(headers, [_normalise_value(value) for value in row])))
    return headers, rows


def _parse_datetime(value, field_name):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        parsed = parse_datetime(text)
        if parsed is None:
            parsed_date = parse_date(text)
            parsed = datetime.combine(parsed_date, time.min) if parsed_date else None
        if parsed is None:
            for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
    if parsed is None:
        raise ValueError(f"{field_name} is not a valid date")
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def _parse_expected_date(value):
    parsed = _parse_datetime(value, "Expected delivery date")
    return parsed.date() if parsed else None


def _issue(batch, row_number, row, issue_type, messages):
    DataIssue.objects.create(
        batch=batch,
        row_number=row_number,
        order_id=row.get("order_id", ""),
        issue_type=issue_type,
        message="; ".join(messages),
        raw_data=row,
    )


@transaction.atomic
def import_orders(upload, user):
    batch = ImportBatch.objects.create(filename=upload.name, uploaded_by=user)
    try:
        headers, rows = read_upload(upload)
    except Exception as exc:
        batch.status = "failed"
        batch.save(update_fields=["status"])
        raise ValueError(f"Could not read the uploaded file: {exc}") from exc

    missing_columns = REQUIRED_COLUMNS - set(headers)
    if missing_columns:
        batch.status = "failed"
        batch.total_rows = len(rows)
        batch.save(update_fields=["status", "total_rows"])
        raise ValueError("Missing required columns: " + ", ".join(sorted(missing_columns)))

    seen_ids = Counter(row.get("order_id", "") for row in rows if row.get("order_id"))
    imported = issues = 0
    for row_number, row in enumerate(rows, start=2):
        errors = []
        required_values = ["order_id", "order_date", "customer_name", "phone_number", "order_value", "payment_type", "order_status", "assigned_employee"]
        for field in required_values:
            if not row.get(field):
                errors.append(f"{field.replace('_', ' ').title()} is required")
        if row.get("order_id") and seen_ids[row["order_id"]] > 1:
            errors.append("Duplicate order ID in this file")
        payment = row.get("payment_type", "").lower()
        if payment and payment not in {"prepaid", "cod"}:
            errors.append("Payment type must be prepaid or COD")
        status = row.get("order_status", "").lower().replace(" ", "_")
        if status and status not in VALID_STATUSES:
            errors.append("Order status is not recognised")
        try:
            order_date = _parse_datetime(row.get("order_date"), "Order date")
        except ValueError as exc:
            errors.append(str(exc)); order_date = None
        try:
            dispatch_date = _parse_datetime(row.get("dispatch_date"), "Dispatch date")
        except ValueError as exc:
            errors.append(str(exc)); dispatch_date = None
        try:
            expected = _parse_expected_date(row.get("expected_delivery_date"))
        except ValueError as exc:
            errors.append(str(exc)); expected = None
        try:
            value = Decimal(str(row.get("order_value", "")))
            if value < 0:
                errors.append("Order value cannot be negative")
        except (InvalidOperation, ValueError):
            errors.append("Order value must be a number"); value = Decimal("0")

        if status in {"dispatched", "out_for_delivery", "failed_delivery", "delivered"} and not dispatch_date:
            errors.append("Dispatch date is required for this order status")
        if status in {"dispatched", "out_for_delivery", "failed_delivery"} and not expected:
            errors.append("Expected delivery date is required for an undelivered shipment")
        if errors:
            kind = "duplicate" if any("Duplicate" in item for item in errors) else "validation"
            _issue(batch, row_number, row, kind, errors)
            issues += 1
            continue

        employee, _ = Employee.objects.get_or_create(name=row["assigned_employee"].strip())
        order, created = Order.objects.update_or_create(
            order_id=row["order_id"].strip(),
            defaults={
                "order_date": order_date,
                "customer_name": row["customer_name"].strip(),
                "phone_number": row["phone_number"].strip(),
                "order_value": value,
                "payment_type": payment,
                "status": status,
                "dispatch_date": dispatch_date,
                "expected_delivery_date": expected,
                "assigned_employee": employee,
                "source_batch": batch,
            },
        )
        imported += 1
        if not created:
            AuditLog.objects.create(order_id=order.order_id, action="Order refreshed from import", details={"batch": batch.filename}, responsible_user=user)

    batch.total_rows = len(rows)
    batch.imported_rows = imported
    batch.issue_rows = issues
    batch.status = "partial" if issues else "completed"
    batch.save()
    run_rule_engine(user=user)
    return batch


def get_rule_values():
    defaults = {
        "unconfirmed_hours": (Decimal("2"), "Unconfirmed threshold", "hours", "Flag unconfirmed orders older than this."),
        "dispatch_hours": (Decimal("24"), "Dispatch threshold", "hours", "Flag confirmed orders not dispatched within this time."),
        "high_value_threshold": (Decimal("5000"), "High-value threshold", "INR", "Escalate overdue undelivered orders above this value."),
    }
    values = {}
    for key, (value, label, unit, description) in defaults.items():
        rule, _ = RuleSetting.objects.get_or_create(key=key, defaults={"value": value, "label": label, "unit": unit, "description": description})
        values[key] = rule.value
    return values


def _upsert_task(order, category, reason, priority, action, deadline, user, active_keys):
    active_keys.add((order.id, category))
    task, created = ExceptionTask.objects.get_or_create(
        order=order,
        category=category,
        defaults={
            "reason": reason,
            "priority": priority,
            "action_required": action,
            "deadline": deadline,
            "assigned_employee": order.assigned_employee,
        },
    )
    if created:
        AuditLog.objects.create(
            task=task,
            order_id=order.order_id,
            action="Exception created",
            details={"category": category, "priority": priority, "assigned_to": order.assigned_employee.name if order.assigned_employee else "Unassigned"},
            responsible_user=user,
        )
    else:
        # A rule rerun may refresh its explanation, but it must never move an
        # existing SLA forward or overwrite a manager's deliberate assignment.
        changed_fields = []
        for field, value in {"reason": reason, "priority": priority, "action_required": action}.items():
            if getattr(task, field) != value:
                setattr(task, field, value)
                changed_fields.append(field)
        if changed_fields:
            task.save(update_fields=changed_fields + ["updated_at"])
    return task


@transaction.atomic
def run_rule_engine(user=None):
    values = get_rule_values()
    now = timezone.now()
    today = timezone.localdate()
    active_keys = set()
    for order in Order.objects.select_related("assigned_employee").all():
        if order.status == "unconfirmed" and now - order.order_date > timedelta(hours=float(values["unconfirmed_hours"])):
            _upsert_task(order, "unconfirmed", f"Order has remained unconfirmed for more than {values['unconfirmed_hours']:g} hours.", "medium", "Call the customer and confirm order intent.", now + timedelta(hours=2), user, active_keys)
        if order.status == "confirmed" and not order.dispatch_date and now - order.order_date > timedelta(hours=float(values["dispatch_hours"])):
            _upsert_task(order, "dispatch_delay", f"Confirmed order was not dispatched within {values['dispatch_hours']:g} hours.", "medium", "Check stock and hand over to the fulfilment team.", now + timedelta(hours=4), user, active_keys)
        if order.status in {"dispatched", "out_for_delivery", "failed_delivery"} and order.expected_delivery_date and order.expected_delivery_date < today:
            _upsert_task(order, "shipment_delay", f"Expected delivery date was {order.expected_delivery_date:%d %b %Y} and the order is still undelivered.", "medium", "Contact the courier and share an updated ETA with the customer.", now + timedelta(hours=6), user, active_keys)
            if order.order_value > values["high_value_threshold"]:
                _upsert_task(order, "high_value", f"High-value order worth ₹{order.order_value:,.0f} is overdue and undelivered.", "high", "Escalate to the operations manager and contact the customer immediately.", now + timedelta(hours=1), user, active_keys)
        if order.status == "failed_delivery":
            _upsert_task(order, "failed_delivery", "Courier recorded a failed delivery attempt.", "high", "Call the customer, verify address and schedule re-attempt.", now + timedelta(hours=1), user, active_keys)

    for task in ExceptionTask.objects.exclude(status="resolved"):
        if (task.order_id, task.category) not in active_keys:
            old_status = task.status
            task.status = "resolved"
            task.resolved_at = now
            task.save(update_fields=["status", "resolved_at", "updated_at"])
            AuditLog.objects.create(task=task, order_id=task.order.order_id, action="Auto-resolved", details={"from": old_status, "reason": "Order no longer matches rule"}, responsible_user=user)
    return len(active_keys)
