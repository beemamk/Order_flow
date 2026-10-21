from django.conf import settings
from django.db import models
from django.utils import timezone


class Employee(models.Model):
    name = models.CharField(max_length=100, unique=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ImportBatch(models.Model):
    STATUS_CHOICES = [("completed", "Completed"), ("partial", "Completed with issues"), ("failed", "Failed")]
    filename = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    total_rows = models.PositiveIntegerField(default=0)
    imported_rows = models.PositiveIntegerField(default=0)
    issue_rows = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="completed")

    class Meta:
        ordering = ["-uploaded_at"]


class Order(models.Model):
    PAYMENT_CHOICES = [("prepaid", "Prepaid"), ("cod", "COD")]
    STATUS_CHOICES = [
        ("unconfirmed", "Unconfirmed"),
        ("confirmed", "Confirmed"),
        ("dispatched", "Dispatched"),
        ("out_for_delivery", "Out for delivery"),
        ("failed_delivery", "Failed delivery"),
        ("delivered", "Delivered"),
        ("cancelled", "Cancelled"),
    ]
    order_id = models.CharField(max_length=50, unique=True, db_index=True)
    order_date = models.DateTimeField()
    customer_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=30)
    order_value = models.DecimalField(max_digits=12, decimal_places=2)
    payment_type = models.CharField(max_length=10, choices=PAYMENT_CHOICES)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES)
    dispatch_date = models.DateTimeField(null=True, blank=True)
    expected_delivery_date = models.DateField(null=True, blank=True)
    assigned_employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    source_batch = models.ForeignKey(ImportBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-order_date"]

    def __str__(self):
        return self.order_id


class DataIssue(models.Model):
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="issues")
    row_number = models.PositiveIntegerField()
    order_id = models.CharField(max_length=50, blank=True)
    issue_type = models.CharField(max_length=40)
    message = models.TextField()
    raw_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["row_number"]


class RuleSetting(models.Model):
    key = models.CharField(max_length=60, unique=True)
    label = models.CharField(max_length=100)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=20)
    description = models.CharField(max_length=255)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.label


class ExceptionTask(models.Model):
    CATEGORY_CHOICES = [
        ("unconfirmed", "Unconfirmed order"),
        ("dispatch_delay", "Dispatch delay"),
        ("shipment_delay", "Shipment delay"),
        ("failed_delivery", "Failed delivery"),
        ("high_value", "High-value delay"),
    ]
    PRIORITY_CHOICES = [("high", "High"), ("medium", "Medium"), ("low", "Low")]
    STATUS_CHOICES = [("open", "Open"), ("in_progress", "In Progress"), ("resolved", "Resolved")]
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="exceptions")
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    reason = models.TextField()
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES)
    action_required = models.CharField(max_length=255)
    deadline = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    assigned_employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    detected_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-detected_at"]
        constraints = [models.UniqueConstraint(fields=["order", "category"], name="unique_order_exception_category")]

    @property
    def is_overdue(self):
        return self.status != "resolved" and self.deadline < timezone.now()

    def __str__(self):
        return f"{self.order.order_id} - {self.get_category_display()}"


class AuditLog(models.Model):
    task = models.ForeignKey(ExceptionTask, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs")
    order_id = models.CharField(max_length=50)
    action = models.CharField(max_length=80)
    details = models.JSONField(default=dict)
    responsible_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
