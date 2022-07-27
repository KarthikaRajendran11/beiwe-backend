from django.views.decorators.http import require_GET
from django.http import HttpResponse

@require_GET
def health_check_api(request: None):
    return HttpResponse("OK")