from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("tasks/", views.task_list, name="task_list"),
    path("tasks/<int:pk>/", views.task_detail, name="task_detail"),
    path("orders/", views.order_list, name="order_list"),
    path("imports/", views.import_data, name="import_data"),
    path("imports/<int:pk>/", views.import_detail, name="import_detail"),
    path("rules/run/", views.rerun_rules, name="rerun_rules"),
    path("rules/", views.rule_settings, name="rule_settings"),
    path("reports/", views.reports, name="reports"),
    path("reports/daily.csv", views.report_csv, name="report_csv"),
    path("audit/", views.audit_trail, name="audit_trail"),
]
