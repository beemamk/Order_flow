import csv
import io
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AuditLog, DataIssue, Employee, ExceptionTask, Order, RuleSetting
from .services import import_orders, run_rule_engine


class RuleEngineTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("manager", password="manager123")
        self.employee = Employee.objects.create(name="Aisha Khan")

    def order(self, **overrides):
        data = {
            "order_id": "ORD-T-001", "order_date": timezone.now() - timedelta(hours=30),
            "customer_name": "Test Customer", "phone_number": "9999999999", "order_value": Decimal("1000"),
            "payment_type": "prepaid", "status": "confirmed", "assigned_employee": self.employee,
        }
        data.update(overrides)
        return Order.objects.create(**data)

    def test_rules_create_expected_and_deduplicated_tasks(self):
        self.order()
        run_rule_engine(self.user)
        task = ExceptionTask.objects.get(category="dispatch_delay")
        first_deadline = task.deadline
        reassigned = Employee.objects.create(name="Rahul Nair")
        task.assigned_employee = reassigned
        task.save(update_fields=["assigned_employee"])
        run_rule_engine(self.user)
        self.assertEqual(ExceptionTask.objects.filter(category="dispatch_delay").count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="Exception created").count(), 1)
        task.refresh_from_db()
        self.assertEqual(task.deadline, first_deadline)
        self.assertEqual(task.assigned_employee, reassigned)

    def test_high_value_overdue_creates_two_tasks(self):
        self.order(status="dispatched", order_value=Decimal("7000"), dispatch_date=timezone.now() - timedelta(days=6), expected_delivery_date=timezone.localdate() - timedelta(days=1))
        run_rule_engine(self.user)
        self.assertSetEqual(set(ExceptionTask.objects.values_list("category", flat=True)), {"shipment_delay", "high_value"})

    def test_rule_change_changes_dispatch_threshold(self):
        self.order(order_date=timezone.now() - timedelta(hours=15))
        run_rule_engine(self.user)
        self.assertFalse(ExceptionTask.objects.exists())
        rule = RuleSetting.objects.get(key="dispatch_hours")
        rule.value = 12
        rule.save()
        run_rule_engine(self.user)
        self.assertTrue(ExceptionTask.objects.filter(category="dispatch_delay").exists())


class ImportAndViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("manager", password="manager123")
        self.client.force_login(self.user)

    def test_import_separates_valid_duplicate_and_invalid_rows(self):
        output = io.StringIO()
        columns = ["order_id", "order_date", "customer_name", "phone_number", "order_value", "payment_type", "order_status", "dispatch_date", "expected_delivery_date", "assigned_employee"]
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        base = {"order_date": (timezone.now() - timedelta(hours=4)).isoformat(), "customer_name": "A", "phone_number": "999", "order_value": "1000", "payment_type": "cod", "order_status": "unconfirmed", "dispatch_date": "", "expected_delivery_date": "", "assigned_employee": "Aisha"}
        writer.writerow({"order_id": "VALID-1", **base})
        writer.writerow({"order_id": "DUP-1", **base})
        writer.writerow({"order_id": "DUP-1", **base})
        writer.writerow({"order_id": "BAD-DATE", **base, "order_date": "wrong"})
        upload = SimpleUploadedFile("orders.csv", output.getvalue().encode(), content_type="text/csv")
        batch = import_orders(upload, self.user)
        self.assertEqual(batch.imported_rows, 1)
        self.assertEqual(batch.issue_rows, 3)
        self.assertEqual(DataIssue.objects.count(), 3)

    def test_task_update_creates_audit_log(self):
        employee = Employee.objects.create(name="Aisha")
        other = Employee.objects.create(name="Rahul")
        order = Order.objects.create(order_id="O-1", order_date=timezone.now(), customer_name="A", phone_number="1", order_value=100, payment_type="cod", status="unconfirmed", assigned_employee=employee)
        task = ExceptionTask.objects.create(order=order, category="unconfirmed", reason="Reason", priority="medium", action_required="Call", deadline=timezone.now() + timedelta(hours=1), assigned_employee=employee)
        response = self.client.post(reverse("task_detail", args=[task.pk]), {"assigned_employee": other.pk, "status": "in_progress"})
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.assigned_employee, other)
        self.assertEqual(task.status, "in_progress")
        self.assertTrue(AuditLog.objects.filter(task=task, action="Task updated").exists())

    def test_daily_report_download(self):
        response = self.client.get(reverse("report_csv"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")

    def test_rtf_formatted_csv_import(self):
        rtf_csv = (
            "{\\rtf1\\ansi\\ansicpg1252\\cocoartf2822\n"
            "\\f0\\fs24 \\cf0 order_id,order_date,customer_name,phone_number,order_value,payment_type,order_status,dispatch_date,expected_delivery_date,assigned_employee\\\n"
            "ORD-999,2026-10-21 08:00:00,Aisha Khan,9876543001,1500,Prepaid,Unconfirmed,,2026-10-23,Aisha Khan}\n"
        )
        upload = SimpleUploadedFile("orders.csv", rtf_csv.encode("utf-8"))
        batch = import_orders(upload, self.user)
        self.assertEqual(batch.total_rows, 1)
        self.assertEqual(batch.imported_rows, 1)
        self.assertTrue(Order.objects.filter(order_id="ORD-999").exists())
