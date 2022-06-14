import json

from django.http import FileResponse
from django.http.response import HttpResponse
from django.views.decorators.http import require_POST

from authentication.data_access_authentication import (api_credential_check,
    api_study_credential_check)
from database.user_models import StudyRelation
from libs.internal_types import ApiResearcherRequest, ApiStudyResearcherRequest
from libs.intervention_export import intervention_survey_data, survey_history_export


@require_POST
@api_credential_check
def get_studies(request: ApiResearcherRequest):
    """
    Retrieve a dict containing the object ID and name of all Study objects that the user can access
    If a GET request, access_key and secret_key must be provided in the URL as GET params. If
    a POST request (strongly preferred!), access_key and secret_key must be in the POST
    request body.
    :return: string: JSON-dumped dict {object_id: name}
    """
    return HttpResponse(
        json.dumps(
            dict(StudyRelation.objects.filter(
                researcher=request.api_researcher).values_list("study__object_id", "study__name")
            )
        )
    )


@require_POST
@api_study_credential_check()
def get_users_in_study(request: ApiStudyResearcherRequest):
    # json can't operate on queryset, need as list.
    return HttpResponse(
        json.dumps(list(request.api_study.participants.values_list('patient_id', flat=True)))
    )


@require_POST
@api_study_credential_check()
def download_study_interventions(request: ApiStudyResearcherRequest):
    return HttpResponse(json.dumps(intervention_survey_data(request.api_study)))


@require_POST
@api_study_credential_check()
def download_study_survey_history(request: ApiStudyResearcherRequest):
    study = request.api_study
    fr = FileResponse(
        survey_history_export(study).decode(),  # okay, whatever, it needs to be a string, not bytes
        content_type="text/json",
        as_attachment=True,
        filename=f"{study.object_id}_surveys_history_data.json",
    )
    fr.set_headers(None)  # django is still stupid?
    return fr
