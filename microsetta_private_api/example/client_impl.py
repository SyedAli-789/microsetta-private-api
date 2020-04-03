"""
Functions to implement OpenAPI 3.0 interface to access private PHI.

Underlies the resource server in the oauth2 workflow. "Resource Server: The
server hosting user-owned resources that are protected by OAuth2. The resource
server validates the access-token and serves the protected resources."
--https://dzone.com/articles/oauth-20-beginners-guide

Loosely based off examples in
https://realpython.com/flask-connexion-rest-api/#building-out-the-complete-api
and associated file
https://github.com/realpython/materials/blob/master/flask-connexion-rest/version_3/people.py  # noqa: E501
"""

import flask
from flask import render_template, session, redirect
import jwt
import requests
from requests.auth import AuthBase
from urllib.parse import quote

# Authrocket uses RS256 public keys, so you can validate anywhere and safely
# store the key in code. Obviously using this mechanism, we'd have to push code
# to roll the keys, which is not ideal, but you can instead hold this in a
# config somewhere and reload

# Python is dumb, don't put spaces anywhere in this string.
from microsetta_private_api.config_manager import SERVER_CONFIG
from microsetta_private_api.model.vue.vue_factory import VueFactory
from microsetta_private_api.model.vue.vue_field import VueInputField, \
    VueTextAreaField, VueSelectField, VueDateTimePickerField
import importlib.resources as pkg_resources


PUB_KEY = pkg_resources.read_text(
    'microsetta_private_api',
    "authrocket.pubkey")

TOKEN_KEY_NAME = 'token'
WORKFLOW_URL = '/workflow'
HELP_EMAIL = "help@microsetta.edu"

# Client might not technically care who the user is, but if they do, they
# get the token, validate it, and pull email out of it.
def parse_jwt(token):
    decoded = jwt.decode(token, PUB_KEY, algorithms=['RS256'], verify=True)
    return decoded["name"]


def rootpath():
    return redirect("/home")


def home():
    user = None
    acct_id = None
    show_wizard = False

    if TOKEN_KEY_NAME in session:
        try:
            # If user leaves the page open, the token can expire before the
            # session, so if our token goes back we need to force them to login
            # again.
            user = parse_jwt(session[TOKEN_KEY_NAME])
        except jwt.exceptions.ExpiredSignatureError:
            return redirect('/logout')
        workflow_needs, workflow_state = determine_workflow_state()
        acct_id = workflow_state.get("account_id", None)
        show_wizard = False  # workflow_needs != ALL_DONE

    # Note: home.jinja2 sends the user directly to authrocket to complete the
    # login if they aren't logged in yet.
    return render_template('home.jinja2',
                           user=user,
                           acct_id=acct_id,
                           show_wizard=show_wizard,
                           endpoint=SERVER_CONFIG["endpoint"],
                           authrocket_url=SERVER_CONFIG["authrocket_url"])


def authrocket_callback(token):
    session[TOKEN_KEY_NAME] = token
    return redirect("/home")


def logout():
    del session[TOKEN_KEY_NAME]
    return redirect("/home")


# States
NEEDS_REROUTE = "NeedsReroute"
NEEDS_LOGIN = "NeedsLogin"
NEEDS_ACCOUNT = "NeedsAccount"
NEEDS_HUMAN_SOURCE = "NeedsHumanSource"
NEEDS_SAMPLE = "NeedsSample"
NEEDS_PRIMARY_SURVEY = "NeedsPrimarySurvey"
ALL_DONE = "AllDone"


def determine_workflow_state():
    current_state = {}
    if TOKEN_KEY_NAME not in session:
        return NEEDS_LOGIN, current_state

    # Do they need to make an account? YES-> create_acct.html
    needs_reroute, accts_output = ApiRequest.get("/accounts")
    if needs_reroute:
        current_state["reroute"] = accts_output
        return NEEDS_REROUTE, current_state
    if len(accts_output) == 0:
        return NEEDS_ACCOUNT, current_state

    acct_id = accts_output[0]["account_id"]
    current_state['account_id'] = acct_id

    # Do they have a human source? NO-> consent.html
    needs_reroute, sources_output = ApiRequest.get(
        "/accounts/%s/sources" % (acct_id,), params={"source_type": "human"})
    if needs_reroute:
        current_state["reroute"] = sources_output
        return NEEDS_REROUTE, current_state
    if len(sources_output) == 0:
        return NEEDS_HUMAN_SOURCE, current_state

    source_id = sources_output[0]["source_id"]
    current_state['human_source_id'] = source_id

    # Have you taken the primary survey? NO-> main_survey.html
    needs_reroute, surveys_output = ApiRequest.get(
        "/accounts/{0}/sources/{1}/surveys".format(acct_id, source_id))
    if needs_reroute:
        current_state["reroute"] = surveys_output
        return NEEDS_REROUTE, current_state

    has_primary = False
    for survey in surveys_output:
        if survey['survey_template_id'] == 1:
            has_primary = True
    if not has_primary:
        return NEEDS_PRIMARY_SURVEY, current_state

    # ???COVID Survey??? -> covid_survey.html

    # Does the human source have any samples? NO-> kit_sample_association.html
    needs_reroute, samples_output = ApiRequest.get(
        "/accounts/{0}/sources/{1}/samples".format(acct_id, source_id))
    if needs_reroute:
        current_state["reroute"] = surveys_output
        return NEEDS_REROUTE, current_state
    if len(samples_output) == 0:
        return NEEDS_SAMPLE, current_state

    current_state['sample_objs'] = samples_output

    return ALL_DONE, current_state


def workflow():
    next_state, current_state = determine_workflow_state()
    print("Next State:", next_state)
    if next_state == NEEDS_REROUTE:
        return current_state["reroute"]
    elif next_state == NEEDS_LOGIN:
        return redirect("/home")
    elif next_state == NEEDS_ACCOUNT:
        return redirect("/workflow_create_account")
    elif next_state == NEEDS_HUMAN_SOURCE:
        return redirect("/workflow_create_human_source")
    elif next_state == NEEDS_PRIMARY_SURVEY:
        return redirect("/workflow_take_primary_survey")
    elif next_state == NEEDS_SAMPLE:
        return redirect("/workflow_claim_kit_samples")
    elif next_state == ALL_DONE:
        # redirect to the page showing all the samples for this source
        samples_url = "/accounts/{account_id}/sources/{source_id}".format(
            account_id=current_state["account_id"],
            source_id=current_state["human_source_id"])
        return redirect(samples_url)


def get_workflow_create_account():
    next_state, current_state = determine_workflow_state()
    if next_state != NEEDS_ACCOUNT:
        return redirect(WORKFLOW_URL)

    email = parse_jwt(session[TOKEN_KEY_NAME])
    return render_template('create_acct.jinja2',
                           authorized_email=email)


def post_workflow_create_account(body):
    next_state, current_state = determine_workflow_state()
    if next_state == NEEDS_ACCOUNT:
        kit_name = body["kit_name"]
        session['kit_name'] = kit_name

        api_json = {
            # "first_name": body['first_name'],
            "last_name": body['last_name'],
            "email": body['email'],
            "address": {
                "street": body['street'],
                "city": body['city'],
                "state": body['state'],
                "post_code": body['post_code'],
                "country_code": body['country_code']
            },
            "kit_name": kit_name
        }

        do_return, accts_output = ApiRequest.post("/accounts", json=api_json)
        if do_return:
            return accts_output

    return redirect(WORKFLOW_URL)


def get_workflow_create_human_source():
    next_state, current_state = determine_workflow_state()
    if next_state != NEEDS_HUMAN_SOURCE:
        return redirect(WORKFLOW_URL)

    acct_id = current_state["account_id"]
    endpoint = SERVER_CONFIG["endpoint"]
    post_url = endpoint + "/workflow_create_human_source"
    do_return, consent_output = ApiRequest.get(
        "/accounts/{0}/consent".format(acct_id),
        params={"consent_post_url": post_url})

    return_val = consent_output if do_return else \
        consent_output["consent_html"]

    return return_val


def post_workflow_create_human_source(body):
    next_state, current_state = determine_workflow_state()
    if next_state != NEEDS_HUMAN_SOURCE:
        return redirect(WORKFLOW_URL)

    acct_id = current_state["account_id"]
    do_return, consent_output = ApiRequest.post(
        "/accounts/{0}/consent".format(acct_id), json=body)

    if do_return:
        return consent_output


def get_workflow_claim_kit_samples():
    next_state, current_state = determine_workflow_state()
    if next_state != NEEDS_SAMPLE:
        return redirect(WORKFLOW_URL)

    if 'kit_name' in session:
        mock_body = {'kit_name': session['kit_name']}
        return post_workflow_claim_kit_samples(mock_body)
    else:
        return render_template("kit_sample_association.jinja2")


def post_workflow_claim_kit_samples(body):
    next_state, current_state = determine_workflow_state()
    if next_state == NEEDS_SAMPLE:
        acct_id = current_state["account_id"]
        source_id = current_state["human_source_id"]

        # get all the unassociated samples in the provided kit
        kit_name = body["kit_name"]
        do_return, sample_output = ApiRequest.get(
            '/kits', params={'kit_name': kit_name})
        if do_return:
            return sample_output

        # for each sample, associate it to the human source
        for curr_sample_obj in sample_output:
            do_return, sample_output = ApiRequest.post(
                '/accounts/{0}/sources/{1}/samples'.format(acct_id, source_id),
                json={"sample_id": curr_sample_obj["sample_id"]}
            )

            if do_return:
                return sample_output

    return redirect(WORKFLOW_URL)


def get_workflow_fill_primary_survey():
    next_state, current_state = determine_workflow_state()
    if next_state != NEEDS_PRIMARY_SURVEY:
        return redirect(WORKFLOW_URL)

    acct_id = current_state["account_id"]
    source_id = current_state["human_source_id"]
    primary_survey = 1
    do_return, survey_output = ApiRequest.get('/accounts/%s/sources/%s/'
                                'survey_templates/%s' %
                                (acct_id, source_id, primary_survey))
    if do_return:
        return survey_output

    return render_template("survey.jinja2",
                           survey_schema=survey_output[
                               'survey_template_text'])


def post_workflow_fill_primary_survey():
    next_state, current_state = determine_workflow_state()
    if next_state == NEEDS_PRIMARY_SURVEY:
        acct_id = current_state["account_id"]
        source_id = current_state["human_source_id"]

        model = {}
        for x in flask.request.form:
            model[x] = flask.request.form[x]

        do_return, surveys_output = ApiRequest.post(
            "/accounts/%s/sources/%s/surveys" %
                        (acct_id, source_id),
                        json={
                              "survey_template_id": 1,
                              "survey_text": model
                            }
                        )

        if do_return:
            return surveys_output

    return redirect(WORKFLOW_URL)


# def view_account(account_id):
#     if TOKEN_KEY_NAME not in session:
#         return redirect(WORKFLOW_URL)
#
#     sources = ApiRequest.get('/accounts/%s/sources' % account_id)
#     return render_template('account.jinja2',
#                            acct_id=account_id,
#                            sources=sources)


def get_source(account_id, source_id):
    next_state, current_state = determine_workflow_state()
    if next_state != ALL_DONE:
        return redirect(WORKFLOW_URL)

    do_return, samples_output = ApiRequest.get(
        '/accounts/%s/sources/%s/samples' % (account_id, source_id))
    if do_return:
        return samples_output

    return render_template('source.jinja2',
                           acct_id=account_id,
                           source_id=source_id,
                           samples=samples_output)


def get_sample(account_id, source_id, sample_id):
    next_state, current_state = determine_workflow_state()
    if next_state != ALL_DONE:
        return redirect(WORKFLOW_URL)

    do_return, sample_output = ApiRequest.get(
        '/accounts/%s/sources/%s/samples/%s' %
        (account_id, source_id, sample_id))
    if do_return:
        return sample_output

    sample_sites = ["Ear wax", "Forehead", "Fur", "Hair", "Left hand",
                    "Left leg", "Mouth", "Nares", "Nasal mucus", "Right hand",
                    "Right leg", "Stool", "Tears", "Torso", "Vaginal mucus"]

    factory = VueFactory()

    schema = factory.start_group("Edit Sample Information")\
        .add_field(VueInputField("sample_barcode", "Barcode")
                   .set(disabled=True))\
        .add_field(VueDateTimePickerField("sample_datetime", "Date and Time")
                   .set(required=True))\
        .add_field(VueSelectField("sample_site", "Site", sample_sites)
                   .set(required=True)) \
        .add_field(VueTextAreaField("sample_notes", "Notes")) \
        .end_group()\
        .build()

    return render_template('sample.jinja2',
                           acct_id=account_id,
                           source_id=source_id,
                           sample=sample_output,
                           schema=schema)


def put_sample(account_id, source_id, sample_id):
    next_state, current_state = determine_workflow_state()
    if next_state != ALL_DONE:
        return redirect(WORKFLOW_URL)

    model = {}
    for x in flask.request.form:
        model[x] = flask.request.form[x]

    do_return, sample_output = ApiRequest.put(
        '/accounts/%s/sources/%s/samples/%s' %
        (account_id, source_id, sample_id),
        json=model)

    if do_return:
        return sample_output

    return redirect('/accounts/%s/sources/%s' %
                    (account_id, source_id))


class BearerAuth(AuthBase):
    def __init__(self, token):
        self.token = token

    def __call__(self, r):
        r.headers['Authorization'] = "Bearer " + self.token
        return r


class ApiRequest:
    API_URL = SERVER_CONFIG["endpoint"] + "/api"
    DEFAULT_PARAMS = {'language_tag': 'en-US'}
    CAfile = SERVER_CONFIG["CAfile"]

    @classmethod
    def build_params(cls, params):
        all_params = {}
        for key in ApiRequest.DEFAULT_PARAMS:
            all_params[key] = ApiRequest.DEFAULT_PARAMS[key]
        if params:
            for key in params:
                all_params[key] = params[key]
        return all_params

    @classmethod
    def _check_response(cls, response):
        do_return = True
        output = None

        if response.status_code == 401:
            # redirect to home page for login
            output = redirect(WORKFLOW_URL)
        elif response.status_code >= 400:
            # redirect to general error page
            error_txt = quote(response.text)
            mailto_url = "mailto:{0}?subject={1}&body={2}".format(
                HELP_EMAIL, quote("minimal interface error"), error_txt)

            output = render_template('error.jinja2',
                                   mailto_url=mailto_url)
        else:
            do_return = False
            if response.text:
                output = response.json()

        return do_return, output

    @classmethod
    def get(cls, path, params=None):
        response = requests.get(ApiRequest.API_URL + path,
                            auth=BearerAuth(session[TOKEN_KEY_NAME]),
                            verify=ApiRequest.CAfile,
                            params=cls.build_params(params))

        return cls._check_response(response)

    @classmethod
    def put(cls, path, params=None, json=None):
        response = requests.put(ApiRequest.API_URL + path,
                            auth=BearerAuth(session[TOKEN_KEY_NAME]),
                            verify=ApiRequest.CAfile,
                            params=cls.build_params(params),
                            json=json)

        return cls._check_response(response)

    @classmethod
    def post(cls, path, params=None, json=None):
        response = requests.post(ApiRequest.API_URL + path,
                             auth=BearerAuth(session[TOKEN_KEY_NAME]),
                             verify=ApiRequest.CAfile,
                             params=cls.build_params(params),
                             json=json)

        return cls._check_response(response)