from django.contrib import admin
from .models import AuditLog, DataIssue, Employee, ExceptionTask, ImportBatch, Order, RuleSetting

admin.site.register([Employee, ImportBatch, Order, DataIssue, ExceptionTask, AuditLog, RuleSetting])
