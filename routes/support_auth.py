import os
import time
import logging
import requests
from flask import Blueprint, request, session, redirect, url_for, render_template
from extensions import csrf

logger = logging.getLogger(__name__)

support_auth_bp = Blueprint('support_auth', __name__)

_CLIENT_ID = os.environ.get('MICROSOFT_CLIENT_ID', '')
_TENANT_ID = os.environ.get('MICROSOFT_TENANT_ID', '')
_CLIENT_SECRET = os.environ.get('MICROSOFT_CLIENT_SECRET', '')

_AUTHORITY = f'https://login.microsoftonline.com/{_TENANT_ID}'
_AUTH_ENDPOINT = f'{_AUTHORITY}/oauth2/v2.0/authorize'
_TOKEN_ENDPOINT = f'{_AUTHORITY}/oauth2/v2.0/token'
_GRAPH_ME = 'https://graph.microsoft.com/v1.0/me'
_SCOPES = 'openid email profile User.Read'
_REDIRECT_PATH = '/support/auth/callback'

SESSION_TIMEOUT = 15 * 60


def _redirect_uri():
    return f'https://support.myticas.com{_REDIRECT_PATH}'


def is_support_authed():
    user = session.get('support_user')
    if not user:
        return False
    last_active = session.get('support_last_active', 0)
    if time.time() - last_active > SESSION_TIMEOUT:
        session.pop('support_user', None)
        session.pop('support_last_active', None)
        logger.info('Support session expired after 15 minutes of inactivity')
        return False
    return True


def refresh_support_session():
    session['support_last_active'] = time.time()


@support_auth_bp.route('/support/auth/login')
@csrf.exempt
def support_login():
    if is_support_authed():
        return redirect('/')
    import secrets
    state = secrets.token_urlsafe(16)
    session['support_oauth_state'] = state
    params = (
        f'?client_id={_CLIENT_ID}'
        f'&response_type=code'
        f'&redirect_uri={_redirect_uri()}'
        f'&scope={_SCOPES.replace(" ", "%20")}'
        f'&state={state}'
        f'&response_mode=query'
    )
    return redirect(_AUTH_ENDPOINT + params)


@support_auth_bp.route('/support/auth/callback')
@csrf.exempt
def support_callback():
    error = request.args.get('error')
    if error:
        logger.error(f'Microsoft OAuth error: {error} — {request.args.get("error_description")}')
        return render_template('support_login.html', error='Sign-in failed. Please try again.'), 401

    code = request.args.get('code')
    state = request.args.get('state')
    if not code or state != session.get('support_oauth_state'):
        logger.warning('Support OAuth: missing code or state mismatch')
        return render_template('support_login.html', error='Invalid sign-in attempt. Please try again.'), 400

    session.pop('support_oauth_state', None)

    token_resp = requests.post(_TOKEN_ENDPOINT, data={
        'client_id': _CLIENT_ID,
        'client_secret': _CLIENT_SECRET,
        'code': code,
        'redirect_uri': _redirect_uri(),
        'grant_type': 'authorization_code',
    }, timeout=10)

    if not token_resp.ok:
        logger.error(f'Token exchange failed: {token_resp.text[:200]}')
        return render_template('support_login.html', error='Authentication failed. Please try again.'), 401

    access_token = token_resp.json().get('access_token')
    if not access_token:
        return render_template('support_login.html', error='Authentication failed. Please try again.'), 401

    me_resp = requests.get(_GRAPH_ME, headers={'Authorization': f'Bearer {access_token}'}, timeout=10)
    if not me_resp.ok:
        logger.error(f'Graph API call failed: {me_resp.text[:200]}')
        return render_template('support_login.html', error='Could not retrieve your profile. Please try again.'), 401

    me = me_resp.json()
    display_name = me.get('displayName') or me.get('givenName', 'User')
    email = me.get('mail') or me.get('userPrincipalName', '')

    session['support_user'] = {'name': display_name, 'email': email}
    session['support_last_active'] = time.time()
    logger.info(f'Support portal login: {email}')
    return redirect('/')


@support_auth_bp.route('/support/auth/logout')
@csrf.exempt
def support_logout():
    name = (session.get('support_user') or {}).get('name', '')
    session.pop('support_user', None)
    session.pop('support_last_active', None)
    logger.info(f'Support portal logout: {name}')
    return redirect('/support/auth/login')
