import csv
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import DashboardFilterForm, TaskUpdateForm, UploadForm
from .models import AuditLog, DataIssue, Employee, ExceptionTask, ImportBatch, Order, RuleSetting
from .services import get_rule_values, import_orders, run_rule_engine


def _filtered_tasks(params):
    tasks = ExceptionTask.objects.select_related("order", "assigned_employee")
    date_from = params.get("date_from")
    date_to = params.get("date_to")
    if date_from:
        tasks = tasks.filter(detected_at__date__gte=date_from)
    if date_to:
        tasks = tasks.filter(detected_at__date__lte=date_to)
    if params.get("category"):
        tasks = tasks.filter(category=params["category"])
    if params.get("employee"):
        tasks = tasks.filter(assigned_employee=params["employee"])
    if params.get("priority"):
        tasks = tasks.filter(priority=params["priority"])
    if params.get("status"):
        tasks = tasks.filter(status=params["status"])
    return tasks


@login_required
def dashboard(request):
    form = DashboardFilterForm(request.GET or None)
    params = form.cleaned_data if form.is_valid() else {}
    tasks = _filtered_tasks(params)
    unresolved = tasks.exclude(status="resolved")
    now = timezone.now()
    category_rows = list(unresolved.values("category").annotate(total=Count("id")).order_by("-total"))
    category_labels = dict(ExceptionTask.CATEGORY_CHOICES)
    category_max = max([row["total"] for row in category_rows] or [1])
    for row in category_rows:
        row["label"] = category_labels.get(row["category"], row["category"])
        row["width"] = round(row["total"] / category_max * 100)
        row["height"] = max(18, round(row["total"] / category_max * 100))
    category_colors = {
        "unconfirmed": "#ffd52e", "dispatch_delay": "#f34a2d", "shipment_delay": "#9160ef",
        "failed_delivery": "#44c394", "high_value": "#5c8bea",
    }
    active_total = sum(row["total"] for row in category_rows)
    gradient_stops = []
    current_percent = 0
    for row in category_rows:
        next_percent = current_percent + (row["total"] / active_total * 100 if active_total else 0)
        color = category_colors.get(row["category"], "#d1d5db")
        gradient_stops.append(f"{color} {current_percent:.1f}% {next_percent:.1f}%")
        current_percent = next_percent
    donut_gradient = ", ".join(gradient_stops) if gradient_stops else "#e5e7eb 0 100%"
    employee_rows = list(
        unresolved.values("assigned_employee__name")
        .annotate(total=Count("id"), high=Count("id", filter=Q(priority="high")))
        .order_by("-total")[:6]
    )
    recent_activity = AuditLog.objects.select_related("responsible_user").all()[:6]
    context = {
        "form": form,
        "today": timezone.localdate(),
        "total_orders": Order.objects.count(),
        "delivered_orders": Order.objects.filter(status="delivered").count(),
        "failed_orders": Order.objects.filter(status="failed_delivery").count(),
        "attention_count": unresolved.values("order_id").distinct().count(),
        "open_count": unresolved.count(),
        "overdue_count": unresolved.filter(deadline__lt=now).count(),
        "resolved_today": tasks.filter(status="resolved", resolved_at__date=timezone.localdate()).count(),
        "data_issue_count": DataIssue.objects.count(),
        "category_rows": category_rows,
        "donut_gradient": donut_gradient,
        "employee_rows": employee_rows,
        "high_priority": unresolved.filter(priority="high").order_by("deadline")[:6],
        "high_priority_count": unresolved.filter(priority="high").count(),
        "recent_activity": recent_activity,
        "active_filter_count": sum(1 for key in ["date_from", "date_to", "category", "employee", "priority"] if params.get(key)),
    }
    return render(request, "operations/dashboard.html", context)


@login_required
def task_list(request):
    form = DashboardFilterForm(request.GET or None)
    params = form.cleaned_data if form.is_valid() else {}
    if request.GET.get("status"):
        params["status"] = request.GET["status"]
    tasks = _filtered_tasks(params)
    search = request.GET.get("q", "").strip()
    if search:
        tasks = tasks.filter(Q(order__order_id__icontains=search) | Q(order__customer_name__icontains=search) | Q(order__phone_number__icontains=search))
    paginator = Paginator(tasks, 20)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "operations/task_list.html", {"form": form, "page": page, "search": search, "current_status": request.GET.get("status", "")})


@login_required
def task_detail(request, pk):
    task = get_object_or_404(ExceptionTask.objects.select_related("order", "assigned_employee"), pk=pk)
    if request.method == "POST":
        old_employee = task.assigned_employee
        old_status = task.status
        form = TaskUpdateForm(request.POST, instance=task)
        if form.is_valid():
            updated = form.save(commit=False)
            changes = {}
            if old_employee != updated.assigned_employee:
                changes["assigned_employee"] = {"from": old_employee.name if old_employee else "Unassigned", "to": updated.assigned_employee.name if updated.assigned_employee else "Unassigned"}
            if old_status != updated.status:
                changes["status"] = {"from": old_status, "to": updated.status}
                updated.resolved_at = timezone.now() if updated.status == "resolved" else None
            updated.save()
            if changes:
                action = "Task reassigned" if "assigned_employee" in changes else "Task status changed"
                if len(changes) > 1:
                    action = "Task updated"
                AuditLog.objects.create(task=updated, order_id=updated.order.order_id, action=action, details=changes, responsible_user=request.user)
                messages.success(request, "Task updated and recorded in the audit trail.")
            else:
                messages.info(request, "No changes were made.")
            return redirect("task_detail", pk=pk)
    else:
        form = TaskUpdateForm(instance=task)
    return render(request, "operations/task_detail.html", {"task": task, "form": form, "history": task.audit_logs.select_related("responsible_user").all()})


@login_required
def order_list(request):
    orders = Order.objects.select_related("assigned_employee").annotate(exception_count=Count("exceptions", filter=~Q(exceptions__status="resolved")))
    search = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if search:
        orders = orders.filter(Q(order_id__icontains=search) | Q(customer_name__icontains=search) | Q(phone_number__icontains=search))
    if status:
        orders = orders.filter(status=status)
    page = Paginator(orders, 25).get_page(request.GET.get("page"))
    return render(request, "operations/order_list.html", {"page": page, "search": search, "current_status": status, "status_choices": Order.STATUS_CHOICES})


@login_required
def import_data(request):
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                batch = import_orders(form.cleaned_data["file"], request.user)
                if batch.issue_rows:
                    messages.warning(request, f"Imported {batch.imported_rows} rows. {batch.issue_rows} rows need correction.")
                else:
                    messages.success(request, f"Successfully imported {batch.imported_rows} orders and ran all rules.")
                return redirect("import_detail", pk=batch.pk)
            except ValueError as exc:
                messages.error(request, str(exc))
    else:
        form = UploadForm()
    return render(request, "operations/import.html", {"form": form, "batches": ImportBatch.objects.all()[:8]})


@login_required
def import_detail(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    return render(request, "operations/import_detail.html", {"batch": batch, "issues": batch.issues.all()})


@login_required
@require_POST
def rerun_rules(request):
    count = run_rule_engine(request.user)
    messages.success(request, f"Rule engine completed. {count} active exception matches evaluated.")
    return redirect(request.POST.get("next") or "dashboard")


@login_required
def reports(request):
    today = timezone.localdate()
    now = timezone.now()
    unresolved = ExceptionTask.objects.exclude(status="resolved")
    context = {
        "new_count": ExceptionTask.objects.filter(detected_at__date=today).count(),
        "unresolved_count": unresolved.count(),
        "overdue_count": unresolved.filter(deadline__lt=now).count(),
        "high_count": unresolved.filter(priority="high").count(),
        "employee_rows": unresolved.values("assigned_employee__name").annotate(total=Count("id")).order_by("-total"),
        "preview": unresolved.select_related("order", "assigned_employee").order_by("-priority", "deadline")[:10],
    }
    return render(request, "operations/reports.html", context)


@login_required
def report_csv(request):
    today = timezone.localdate()
    now = timezone.now()
    tasks = ExceptionTask.objects.select_related("order", "assigned_employee").filter(
        Q(detected_at__date=today) | ~Q(status="resolved") | Q(deadline__lt=now, status__in=["open", "in_progress"])
    ).distinct().order_by("-priority", "deadline")
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="daily-exception-report-{today.isoformat()}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Order ID", "Customer", "Phone", "Exception", "Priority", "Assigned Employee", "Action Required", "Deadline", "Task Status", "Overdue", "Detected At"])
    for task in tasks:
        writer.writerow([
            task.order.order_id, task.order.customer_name, task.order.phone_number, task.get_category_display(), task.get_priority_display(),
            task.assigned_employee.name if task.assigned_employee else "Unassigned", task.action_required,
            timezone.localtime(task.deadline).strftime("%Y-%m-%d %H:%M"), task.get_status_display(), "Yes" if task.is_overdue else "No",
            timezone.localtime(task.detected_at).strftime("%Y-%m-%d %H:%M"),
        ])
    return response


@login_required
def audit_trail(request):
    logs = AuditLog.objects.select_related("responsible_user", "task")
    search = request.GET.get("q", "").strip()
    if search:
        logs = logs.filter(Q(order_id__icontains=search) | Q(action__icontains=search))
    page = Paginator(logs, 30).get_page(request.GET.get("page"))
    return render(request, "operations/audit.html", {"page": page, "search": search})


@login_required
def rule_settings(request):
    get_rule_values()
    rules = RuleSetting.objects.all().order_by("id")
    if request.method == "POST":
        changed = []
        for rule in rules:
            raw = request.POST.get(rule.key, "")
            try:
                value = float(raw)
            except ValueError:
                messages.error(request, f"Enter a valid number for {rule.label}.")
                return redirect("rule_settings")
            if value <= 0:
                messages.error(request, f"{rule.label} must be greater than zero.")
                return redirect("rule_settings")
            if float(rule.value) != value:
                old = str(rule.value)
                rule.value = value
                rule.save(update_fields=["value", "updated_at"])
                changed.append({"rule": rule.key, "from": old, "to": str(value)})
        if changed:
            AuditLog.objects.create(order_id="SYSTEM", action="Business rules updated", details={"changes": changed}, responsible_user=request.user)
            run_rule_engine(request.user)
            messages.success(request, "Rules updated and all orders re-evaluated.")
        else:
            messages.info(request, "No rule values changed.")
        return redirect("rule_settings")
    return render(request, "operations/rules.html", {"rules": rules})
