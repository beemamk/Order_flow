from .models import ExceptionTask


def app_context(request):
    if not request.user.is_authenticated:
        return {}
    return {"sidebar_open_tasks": ExceptionTask.objects.exclude(status="resolved").count()}
