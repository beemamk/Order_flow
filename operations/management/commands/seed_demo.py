import csv
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from operations.models import AuditLog, DataIssue, Employee, ExceptionTask, ImportBatch, Order
from operations.services import get_rule_values, run_rule_engine


class Command(BaseCommand):
    help = "Create a complete, repeatable demo workspace and sample import file."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Clear existing operational data first.")

    def handle(self, *args, **options):
        if options["reset"]:
            AuditLog.objects.all().delete()
            ExceptionTask.objects.all().delete()
            DataIssue.objects.all().delete()
            Order.objects.all().delete()
            ImportBatch.objects.all().delete()
            Employee.objects.all().delete()

        user_model = get_user_model()
        manager, _ = user_model.objects.get_or_create(username="manager", defaults={"first_name": "Demo", "last_name": "Manager", "email": "manager@orderflow.local", "is_staff": True})
        manager.set_password("manager123")
        manager.save()

        employee_names = ["Aisha Khan", "Rahul Nair", "Meera Iyer", "Vikram Shah", "Neha Menon"]
        employees = [Employee.objects.get_or_create(name=name, defaults={"email": name.lower().replace(" ", ".") + "@orderflow.local"})[0] for name in employee_names]
        now = timezone.now()
        rows = []
        names = ["Aarav Sharma", "Diya Patel", "Arjun Menon", "Ishita Rao", "Kabir Singh", "Ananya Das", "Rohan Gupta", "Maya Nair", "Dev Mehta", "Sara Joseph"]

        for index in range(120):
            order_id = f"ORD-2026-{index + 1:04d}"
            employee = employees[index % len(employees)]
            order_date = now - timedelta(days=index % 18, hours=(index * 3) % 22)
            dispatch_date = order_date + timedelta(hours=8)
            expected = (order_date + timedelta(days=4)).date()
            value = Decimal(799 + (index * 437) % 8200)
            status = "delivered"

            if 70 <= index < 80:
                status = "unconfirmed"
                order_date = now - timedelta(hours=4 + index - 70)
                dispatch_date = None
                expected = None
            elif 80 <= index < 90:
                status = "confirmed"
                order_date = now - timedelta(hours=28 + index - 80)
                dispatch_date = None
                expected = (now + timedelta(days=3)).date()
            elif 90 <= index < 100:
                status = "dispatched"
                order_date = now - timedelta(days=7 + index % 3)
                dispatch_date = order_date + timedelta(hours=10)
                expected = (now - timedelta(days=1 + index % 3)).date()
                value = Decimal(6200 + ((index - 90) * 510)) if index % 2 == 0 else Decimal(2400 + index)
            elif 100 <= index < 110:
                status = "failed_delivery"
                order_date = now - timedelta(days=3)
                dispatch_date = order_date + timedelta(hours=9)
                expected = (now + timedelta(days=1)).date()
            elif index >= 110:
                status = "confirmed" if index % 2 else "dispatched"
                order_date = now - timedelta(hours=3 + index % 6)
                dispatch_date = None if status == "confirmed" else order_date + timedelta(hours=1)
                expected = (now + timedelta(days=3)).date()

            order, _ = Order.objects.update_or_create(
                order_id=order_id,
                defaults={
                    "order_date": order_date,
                    "customer_name": names[index % len(names)],
                    "phone_number": f"+91 98{(10000000 + index * 7919) % 100000000:08d}",
                    "order_value": value,
                    "payment_type": "cod" if index % 3 == 0 else "prepaid",
                    "status": status,
                    "dispatch_date": dispatch_date,
                    "expected_delivery_date": expected,
                    "assigned_employee": employee,
                },
            )
            rows.append(order)

        get_rule_values()
        run_rule_engine(manager)
        # Keep a small set outside SLA so the overdue workflow and report are
        # visible immediately in a fresh demonstration workspace.
        for offset, task in enumerate(ExceptionTask.objects.filter(priority="medium").order_by("detected_at")[:8], start=1):
            task.deadline = now - timedelta(hours=offset)
            task.status = "in_progress" if offset <= 3 else "open"
            task.save(update_fields=["deadline", "status", "updated_at"])
        self._write_sample_csv(rows)
        self.stdout.write(self.style.SUCCESS(f"Demo ready: {Order.objects.count()} orders, {ExceptionTask.objects.count()} exception tasks."))
        self.stdout.write("Sign in with manager / manager123")

    def _write_sample_csv(self, orders):
        target = Path("static/sample/sample_orders.csv")
        target.parent.mkdir(parents=True, exist_ok=True)
        columns = ["order_id", "order_date", "customer_name", "phone_number", "order_value", "payment_type", "order_status", "dispatch_date", "expected_delivery_date", "assigned_employee"]
        with target.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for order in orders:
                writer.writerow({
                    "order_id": order.order_id,
                    "order_date": order.order_date.isoformat(),
                    "customer_name": order.customer_name,
                    "phone_number": order.phone_number,
                    "order_value": order.order_value,
                    "payment_type": order.payment_type,
                    "order_status": order.status,
                    "dispatch_date": order.dispatch_date.isoformat() if order.dispatch_date else "",
                    "expected_delivery_date": order.expected_delivery_date.isoformat() if order.expected_delivery_date else "",
                    "assigned_employee": order.assigned_employee.name,
                })
            writer.writerow({"order_id": "ORD-ERR-001", "order_date": "not-a-date", "customer_name": "Invalid Date", "phone_number": "9999999999", "order_value": "1200", "payment_type": "prepaid", "order_status": "confirmed", "dispatch_date": "", "expected_delivery_date": "", "assigned_employee": "Aisha Khan"})
            writer.writerow({"order_id": "", "order_date": timezone.now().isoformat(), "customer_name": "Missing ID", "phone_number": "9999999998", "order_value": "900", "payment_type": "cod", "order_status": "unconfirmed", "dispatch_date": "", "expected_delivery_date": "", "assigned_employee": "Rahul Nair"})
            duplicate = {"order_id": "ORD-DUP-001", "order_date": timezone.now().isoformat(), "customer_name": "Duplicate Customer", "phone_number": "9999999997", "order_value": "750", "payment_type": "cod", "order_status": "unconfirmed", "dispatch_date": "", "expected_delivery_date": "", "assigned_employee": "Meera Iyer"}
            writer.writerow(duplicate)
            writer.writerow(duplicate)
