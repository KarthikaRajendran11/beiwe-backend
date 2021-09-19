from django.http.request import HttpRequest
from django.shortcuts import redirect, render
from middleware.admin_authentication import logout_researcher

from authentication.admin_authentication import (authenticate_researcher_login,
    authenticate_researcher_study_access, get_researcher_allowed_studies,
    get_researcher_allowed_studies_as_query_set, SESSION_NAME)
from constants.admin_pages import (DisableApiKeyForm, NEW_API_KEY_MESSAGE, NewApiKeyForm,
    RESET_DOWNLOAD_API_CREDENTIALS_MESSAGE)
from database.security_models import ApiKey
from database.study_models import Study
from database.user_models import Researcher
from libs.firebase_config import check_firebase_instance
from libs.security import check_password_requirements
from libs.serializers import ApiKeySerializer


####################################################################################################
############################################# Basics ###############################################
####################################################################################################


def logout_admin(request: HttpRequest):
    """ clear session information for a researcher """
    logout_researcher(request)
    return redirect("/")

####################################################################################################
###################################### Endpoints ###################################################
####################################################################################################


# @admin_pages.route('/choose_study', methods=['GET'])
@authenticate_researcher_login
def choose_study(request: HttpRequest):
    allowed_studies = get_researcher_allowed_studies_as_query_set(request)

    # If the admin is authorized to view exactly 1 study, redirect to that study,
    # Otherwise, show the "Choose Study" page
    if allowed_studies.count() == 1:
        return redirect('/view_study/{:d}'.format(allowed_studies.values_list('pk', flat=True).get()))

    return render(
        request,
        'choose_study.html',
        context=dict(
            studies=[obj.as_unpacked_native_python() for obj in allowed_studies],
            is_admin=request.researcher_is_an_admin(),
        )
    )


# @admin_pages.route('/view_study/<string:study_id>', methods=['GET'])
@authenticate_researcher_study_access
def view_study(request: HttpRequest, study_id=None):
    study = Study.objects.get(pk=study_id)

    return render(
        'view_study.html',
        study=study,
        audio_survey_ids=study.get_survey_ids_and_object_ids('audio_survey'),
        image_survey_ids=study.get_survey_ids_and_object_ids('image_survey'),
        tracking_survey_ids=study.get_survey_ids_and_object_ids('tracking_survey'),
        # these need to be lists because they will be converted to json.
        study_fields=list(study.fields.all().values_list('field_name', flat=True)),
        interventions=list(study.interventions.all().values_list("name", flat=True)),
        page_location='study_landing',
        study_id=study_id,
        is_site_admin=request.session_researcher.site_admin,
        push_notifications_enabled=check_firebase_instance(require_android=True) or
                                   check_firebase_instance(require_ios=True),
    )


# @admin_pages.route('/manage_credentials')
@authenticate_researcher_login
def manage_credentials(request: HttpRequest):
    serializer = ApiKeySerializer(ApiKey.objects.filter(researcher=get_session_researcher()), many=True)
    return render(
        'manage_credentials.html',
        is_admin=researcher_is_an_admin(),
        api_keys=sorted(serializer.data, reverse=True, key=lambda x: x['created_on']),
    )


# @admin_pages.route('/reset_admin_password', methods=['POST'])
@authenticate_researcher_login
def reset_admin_password(request: HttpRequest):
    username = request.session[SESSION_NAME]
    current_password = request.values['current_password']
    new_password = request.values['new_password']
    confirm_new_password = request.values['confirm_new_password']

    if not Researcher.check_password(username, current_password):
        flash("The Current Password you have entered is invalid", 'danger')
        return redirect('/manage_credentials')
    if not check_password_requirements(new_password, flash_message=True):
        return redirect("/manage_credentials")
    if new_password != confirm_new_password:
        flash("New Password does not match Confirm New Password", 'danger')
        return redirect('/manage_credentials')

    # todo: sanitize password?
    Researcher.objects.get(username=username).set_password(new_password)
    flash("Your password has been reset!", 'success')
    return redirect('/manage_credentials')


# @admin_pages.route('/reset_download_api_credentials', methods=['POST'])
@authenticate_researcher_login
def reset_download_api_credentials(request: HttpRequest):
    researcher = Researcher.objects.get(username=session[SESSION_NAME])
    access_key, secret_key = researcher.reset_access_credentials()
    flash(Markup(RESET_DOWNLOAD_API_CREDENTIALS_MESSAGE % (access_key, secret_key)), 'warning')
    return redirect("/manage_credentials")


# @admin_pages.route('/new_api_key', methods=['POST'])
@authenticate_researcher_login
def new_api_key(request: HttpRequest):
    form = NewApiKeyForm(request.values)
    if not form.is_valid():
        return redirect(url_for("admin_pages.manage_credentials"))
    researcher = Researcher.objects.get(username=session[SESSION_NAME])

    api_key = ApiKey.generate(
        researcher=researcher,
        has_tableau_api_permissions=form.cleaned_data['tableau_api_permission'],
        readable_name=form.cleaned_data['readable_name'],
    )
    # noinspection StrFormat
    msg = NEW_API_KEY_MESSAGE % (api_key.access_key_id, api_key.access_key_secret_plaintext)
    flash(Markup(msg), 'warning')
    return redirect(url_for("admin_pages.manage_credentials"))


# @admin_pages.route('/disable_api_key', methods=['POST'])
@authenticate_researcher_login
def disable_api_key(request: HttpRequest):
    form = DisableApiKeyForm(request.values)
    if not form.is_valid():
        return redirect("/manage_credentials")

    api_key_id = request.values["api_key_id"]
    api_key_query = ApiKey.objects.filter(access_key_id=api_key_id).filter(researcher=get_session_researcher())
    if not api_key_query.exists():
        flash(Markup("No matching API key found to disable"), 'warning')
        return redirect("/manage_credentials")
    api_key = api_key_query[0]
    if not api_key.is_active:
        flash("That API key has already been disabled: %s" % api_key_id, 'warning')
        return redirect("/manage_credentials")
    api_key.is_active = False
    api_key.save()
    # flash("The API key %s is now disabled" % str(api_key.access_key_id), 'warning')
    return redirect("/manage_credentials")


