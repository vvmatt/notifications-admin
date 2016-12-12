from bs4 import BeautifulSoup, element
from functools import partial
import pytest
from flask import url_for
from werkzeug.exceptions import InternalServerError
from unittest.mock import Mock, ANY
from freezegun import freeze_time
from tests.conftest import (
    mock_get_services,
    mock_get_services_with_no_services,
    mock_get_services_with_one_service
)
from app.main.views.feedback import has_live_services, in_business_hours


def test_logged_in_user_redirects_to_choose_service(app_,
                                                    api_user_active,
                                                    mock_get_user,
                                                    mock_get_user_by_email,
                                                    mock_login):
    with app_.test_request_context():
        with app_.test_client() as client:
            client.login(api_user_active)
            response = client.get(url_for('main.index'))
            assert response.status_code == 302

            response = client.get(url_for('main.sign_in', follow_redirects=True))
            assert response.location == url_for('main.choose_service', _external=True)


def test_get_support_index_page(client):
    resp = client.get(url_for('main.support'))
    assert resp.status_code == 200


@freeze_time('2016-12-12 12:00:00.000000')
@pytest.mark.parametrize('support_type, expected_h1', [
    ('problem', 'Report a problem'),
    ('question', 'Ask a question or give feedback'),
])
@pytest.mark.parametrize('logged_in, expected_form_field, expected_contact_details', [
    (True, type(None), 'We’ll reply to test@user.gov.uk'),
    (True, type(None), 'We’ll reply to test@user.gov.uk'),
    (False, element.Tag, 'Leave your details below if you\'d like a response.'),
])
def test_choose_support_type(
    client,
    api_user_active,
    mock_get_user,
    mock_get_services,
    logged_in,
    expected_form_field,
    expected_contact_details,
    support_type,
    expected_h1
):
    if logged_in:
        client.login(api_user_active)
    response = client.post(
        url_for('main.support'),
        data={'support_type': support_type}, follow_redirects=True
    )
    assert response.status_code == 200
    page = BeautifulSoup(response.data.decode('utf-8'), 'html.parser')
    assert page.h1.string.strip() == expected_h1
    assert isinstance(page.find('input', {'name': 'name'}), expected_form_field)
    assert isinstance(page.find('input', {'name': 'email_address'}), expected_form_field)
    assert page.find('form').find('p').text.strip() == expected_contact_details


@freeze_time('2016-12-12 12:00:00.000000')
@pytest.mark.parametrize('ticket_type, expected_status_code', [
    ('problem', 200),
    ('question', 200),
    ('gripe', 404)
])
def test_get_feedback_page(app_, ticket_type, expected_status_code):
    with app_.test_request_context():
        with app_.test_client() as client:
            resp = client.get(url_for('main.feedback', ticket_type=ticket_type))
            assert resp.status_code == expected_status_code


@freeze_time("2016-12-12 12:00:00.000000")  # normal working day
@pytest.mark.parametrize('data, expected_message, expected_person_name, expected_email', [
    (
        {'feedback': "blah", 'name': 'Fred'},
        'Environment: http://localhost/\nFred (no email address supplied)\nblah',
        'Fred',
        'donotreply@notifications.service.gov.uk',
    ),
    (
        {'feedback': "blah"},
        'Environment: http://localhost/\n (no email address supplied)\nblah',
        None,
        'donotreply@notifications.service.gov.uk',
    ),
    (
        {'feedback': "blah", 'name': "Steve Irwin", 'email_address': 'rip@gmail.com'},
        'Environment: http://localhost/\n\nblah',
        'Steve Irwin',
        'rip@gmail.com',
    ),
])
@pytest.mark.parametrize('ticket_type', ['problem', 'question'])
def test_post_problem_or_question(
    client,
    mocker,
    ticket_type,
    data,
    expected_message,
    expected_person_name,
    expected_email,
):
    mock_post = mocker.patch(
        'app.main.views.feedback.requests.post',
        return_value=Mock(status_code=201)
    )
    resp = client.post(
        url_for('main.feedback', ticket_type=ticket_type),
        data=data,
    )
    assert resp.status_code == 302
    mock_post.assert_called_with(
        ANY,
        data={
            'department_id': ANY,
            'agent_team_id': ANY,
            'subject': 'Notify feedback',
            'message': expected_message,
            'person_email': expected_email,
            'person_name': expected_person_name,
            'label': ticket_type,
            'urgency': ANY,
        },
        headers=ANY
    )


@pytest.mark.parametrize('ticket_type, severe, is_in_business_hours, expected_urgency', [

    # business hours, always urgent
    ('problem', True, True, 10),
    ('question', True, True, 10),
    ('problem', False, True, 10),
    ('question', False, True, 10),

    # out of hours, non emergency, never urgent
    ('problem', False, False, 1),
    ('question', False, False, 1),

    # out of hours, emergency problems are urgent
    ('problem', True, False, 10),
    ('question', True, False, 1),

])
def test_urgency(
    client,
    api_user_active,
    mock_get_user,
    mock_get_services,
    mocker,
    ticket_type,
    severe,
    is_in_business_hours,
    expected_urgency,
):
    mocker.patch('app.main.views.feedback.in_business_hours', return_value=is_in_business_hours)
    mock_post = mocker.patch('app.main.views.feedback.requests.post', return_value=Mock(status_code=201))
    client.login(api_user_active)
    resp = client.post(
        url_for('main.feedback', ticket_type=ticket_type, severe=severe),
        data={'feedback': "blah"},
    )
    assert resp.status_code == 302
    assert mock_post.call_args[1]['data']['urgency'] == expected_urgency


ids, params = zip(*[
    ('non-logged in users always have to triage', (
        'problem', '2016-12-12 23:59:59.999999', False, True,
        302, 'main.triage'
    )),
    ('trial services are never high priority', (
        'problem', '2016-12-12 23:59:59.999999', True, False,
        200, None
    )),
    ('we can triage in hours', (
        'problem', '2016-12-12 12:00:00.000000', True, True,
        200, None
    )),
    ('only problems are high priority', (
        'question', '2016-12-12 23:59:59.999999', True, True,
        200, None
    )),
    ('should triage out of hours', (
        'problem', '2016-12-12 23:59:59.999999', True, True,
        302, 'main.triage'
    ))
])


@pytest.mark.parametrize(
    'ticket_type, when, logged_in, has_live_services, expected_status, expected_redirect',
    params, ids=ids
)
def test_redirects_to_triage(
    client,
    api_user_active,
    mocker,
    mock_get_user,
    ticket_type,
    when,
    logged_in,
    has_live_services,
    expected_status,
    expected_redirect,
):
    mocker.patch('app.main.views.feedback.has_live_services', return_value=has_live_services)
    if logged_in:
        client.login(api_user_active)
    with freeze_time(when):
        response = client.get(
            url_for('main.feedback', ticket_type=ticket_type),
            data={},
        )
    assert response.status_code == expected_status
    assert response.location == (
        None if expected_redirect is None else url_for(expected_redirect, _external=True)
    )


@pytest.mark.parametrize('get_services_mock, expected_return_value', [
    (mock_get_services, True),
    (mock_get_services_with_no_services, False),
    (mock_get_services_with_one_service, False),
])
def test_has_live_services(
    mocker,
    fake_uuid,
    get_services_mock,
    expected_return_value
):
    get_services_mock(mocker, fake_uuid)
    assert has_live_services(12345) == expected_return_value


@pytest.mark.parametrize('when, is_in_business_hours', [

    ('2016-06-06 09:00:00', False),  # opening time, summer and winter
    ('2016-12-12 09:00:00', False),
    ('2016-06-06 09:00:01', True),
    ('2016-12-12 09:00:01', True),

    ('2016-12-12 12:00:00', True),   # middle of the day

    ('2016-12-12 17:29:59', True),   # closing time
    ('2016-12-12 17:30:00', False),

    ('2016-12-10 12:00:00', False),  # Saturday
    ('2016-12-11 12:00:00', False),  # Sunday
    ('2016-01-01 12:00:00', False),  # Bank holiday

])
def test_in_business_hours(when, is_in_business_hours):
    with freeze_time(when):
        assert in_business_hours() == is_in_business_hours


@pytest.mark.parametrize('choice, expected_redirect_param', [
    ('yes', True),
    ('no', False),
])
def test_triage_redirects_to_correct_url(client, mocker, choice, expected_redirect_param):
    response = client.post(url_for('main.triage'), data={'severe': choice})
    assert response.status_code == 302
    assert response.location == url_for(
        'main.feedback',
        ticket_type='problem',
        severe=expected_redirect_param,
        _external=True,
    )


@pytest.mark.parametrize('when, severe, should_see_bat_email', [
    ('2016-12-12 12:00:00.000000', True, False),
    ('2016-12-12 23:59:59.999999', True, True),
    ('2016-12-12 12:00:00.000000', False, False),
    ('2016-12-12 23:59:59.999999', False, False),
])
def test_should_be_shown_the_bat_email(
    client,
    active_user_with_permissions,
    mocker,
    service_one,
    mock_get_services,
    when,
    severe,
    should_see_bat_email,
):

    feedback_page = url_for('main.feedback', ticket_type='problem', severe=severe)

    with freeze_time(when):
        response = client.get(feedback_page)

        if should_see_bat_email:
            assert response.status_code == 302
            assert response.location == url_for('main.bat_phone', _external=True)
        else:
            assert response.status_code == 200

        # logged in users should never see the bat email page
        client.login(active_user_with_permissions, mocker, service_one)
        logged_in_response = client.get(feedback_page)
        assert logged_in_response.status_code == 200


def test_bat_email_page(
    client,
    active_user_with_permissions,
    mocker,
    service_one,
):

    bat_phone_page = url_for('main.bat_phone')

    response = client.get(bat_phone_page)
    assert response.status_code == 200

    client.login(active_user_with_permissions, mocker, service_one)
    logged_in_response = client.get(bat_phone_page)
    assert logged_in_response.status_code == 302
    assert logged_in_response.location == url_for('main.feedback', ticket_type='problem', _external=True)


@freeze_time('2016-12-12 12:00:00.000000')
@pytest.mark.parametrize('ticket_type', ['problem', 'question'])
def test_post_feedback_with_no_name_or_email(app_, mocker, ticket_type):
    mock_post = mocker.patch(
        'app.main.views.feedback.requests.post',
        return_value=Mock(status_code=201))
    with app_.test_request_context():
        with app_.test_client() as client:
            resp = client.post(
                url_for('main.feedback', ticket_type=ticket_type),
                data={'feedback': "blah"},
            )
            assert resp.status_code == 302
            mock_post.assert_called_with(
                ANY,
                data={
                    'department_id': ANY,
                    'agent_team_id': ANY,
                    'subject': 'Notify feedback',
                    'message': (
                        'Environment: http://localhost/\n'
                        ' (no email address supplied)\nblah'
                    ),
                    'person_email': app_.config['DESKPRO_PERSON_EMAIL'],
                    'person_name': None,
                    'label': ticket_type,
                    'urgency': 10,
                },
                headers=ANY
            )


@pytest.mark.parametrize('ticket_type', ['problem', 'question'])
def test_log_error_on_post(app_, mocker, ticket_type):
    mock_post = mocker.patch(
        'app.main.views.feedback.requests.post',
        return_value=Mock(status_code=201))
    with app_.test_request_context():
        with app_.test_client() as client:
            resp = client.post(
                url_for('main.feedback', ticket_type=ticket_type),
                data={'feedback': "blah", 'name': "Steve Irwin", 'email_address': 'rip@gmail.com'})
            assert resp.status_code == 302
            mock_post.assert_called_with(
                ANY,
                data={
                    'subject': 'Notify feedback',
                    'department_id': ANY,
                    'agent_team_id': ANY,
                    'message': 'Environment: http://localhost/\n\nblah',
                    'person_name': 'Steve Irwin',
                    'person_email': 'rip@gmail.com',
                    'label': ticket_type,
                    'urgency': 1,
                },
                headers=ANY)


@freeze_time('2016-12-12 12:00:00.000000')
@pytest.mark.parametrize('ticket_type', ['problem', 'question'])
def test_log_error_on_post(app_, mocker, ticket_type):
    mock_post = mocker.patch(
        'app.main.views.feedback.requests.post',
        return_value=Mock(
            status_code=401,
            json=lambda: {
                'error_code': 'invalid_auth',
                'error_message': 'Please provide a valid API key or token'}))
    with app_.test_request_context():
        mock_logger = mocker.patch.object(app_.logger, 'error')
        with app_.test_client() as client:
            with pytest.raises(InternalServerError):
                resp = client.post(
                    url_for('main.feedback', ticket_type=ticket_type),
                    data={'feedback': "blah", 'name': "Steve Irwin", 'email_address': 'rip@gmail.com'})
            assert mock_post.called
            mock_logger.assert_called_with(
                "Deskpro create ticket request failed with {} '{}'".format(mock_post().status_code, mock_post().json()))
