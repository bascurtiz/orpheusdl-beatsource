from datetime import timedelta, datetime
import json
from urllib.parse import urlparse, parse_qs

from utils.utils import create_requests_session


class BeatsourceError(Exception):
    def __init__(self, message):
        self.message = message
        super(BeatsourceError, self).__init__(message)


class BeatsourceApi:
    def __init__(self):
        self.API_URL = "https://api.beatsource.com/v4/"

        # client id from Go project (beatportdl-main)
        self.client_id = "ryZ8LuyQVPqbK2mBX2Hwt4qSMtnWuTYSqBPO92yQ"
        self.redirect_uri = "seratodjlite://beatsource"

        self.access_token = None
        self.refresh_token = None
        self.expires = None

        # required for the cookies
        self.s = create_requests_session()

    def headers(self, use_access_token: bool = False):
        return {
            'user-agent': 'orpheusdl/beatsource-module',
            'authorization': f'Bearer {self.access_token}' if use_access_token else None,
        }

    def auth(self, username: str, password: str) -> dict:
        # --- New Auth Flow based on Go project ---
        # 1. Login to get sessionid cookie
        login_url = f"{self.API_URL}auth/login/"
        login_payload = {
            "username": username,
            "password": password,
        }
        login_headers = {
            # Mimic browser user-agent, potentially needed
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        }
        
        print(f"Attempting login via {login_url}...")
        r_login = self.s.post(login_url, json=login_payload, headers=login_headers)

        if r_login.status_code != 200:
            # Check for blank field errors and provide a better message
            try:
                error_data = r_login.json()
                if isinstance(error_data, dict):
                    if "username" in error_data and "password" in error_data:
                        username_errors = error_data.get("username", [])
                        password_errors = error_data.get("password", [])
                        if any("blank" in str(msg).lower() for msg in username_errors) and \
                           any("blank" in str(msg).lower() for msg in password_errors):
                            raise BeatsourceError(
                                "Beatsource credentials are missing in settings.json. "
                                "Please fill in: username, password. "
                                "Use the OrpheusDL GUI Settings tab (Beatsource) or edit config/settings.json directly."
                            )
            except (ValueError, KeyError, json.JSONDecodeError):
                pass  # If JSON parsing fails, fall through to original error
            raise ConnectionError(f"Login failed ({r_login.status_code}): {r_login.text}")

        session_id = self.s.cookies.get('sessionid')
        if not session_id:
             # Check response body for potential errors if cookie not found
            try:
                login_response_json = r_login.json()
                if "non_field_errors" in login_response_json:
                    raise BeatsourceError(f"Login failed: {login_response_json['non_field_errors'][0]}")
            except json.JSONDecodeError:
                pass # Ignore if body isn't JSON
            raise BeatsourceError("Could not find sessionid cookie after successful login attempt.")

        print("Login successful, obtained session ID.")

        # 2. Authorize using sessionid to get the code
        auth_url = f"{self.API_URL}auth/o/authorize/"
        auth_params = {
            "client_id": self.client_id,
            "response_type": "code",
            # NO redirect_uri here compared to previous flow
        }
        auth_headers = {
             "User-Agent": login_headers["User-Agent"], # Use same user agent
             # The requests session `self.s` automatically handles sending cookies
        }
        
        print(f"Attempting authorization via {auth_url} using session ID...")
        # Important: disable redirects to capture the Location header
        r_auth = self.s.get(auth_url, params=auth_params, headers=auth_headers, allow_redirects=False)

        if r_auth.status_code != 302: # Expecting a redirect
            raise ConnectionError(f"Authorization step failed ({r_auth.status_code}), expected 302 redirect. Response: {r_auth.text}")

        redirect_location = r_auth.headers.get('Location')
        if not redirect_location:
            raise BeatsourceError("Authorization step did not return a Location header.")

        # Parse the code from the redirect location
        try:
            parsed_url = urlparse(redirect_location)
            query_params = parse_qs(parsed_url.query)
            code = query_params.get('code', [None])[0]
        except Exception as e:
             raise BeatsourceError(f"Failed to parse authorization code from redirect Location '{redirect_location}': {e}")

        if not code:
            raise BeatsourceError(f"Could not extract authorization code from redirect Location: {redirect_location}")
        
        print("Authorization successful, obtained code.")

        # 3. Exchange the code for tokens
        token_url = f"{self.API_URL}auth/o/token/"
        token_payload = {
            "client_id": self.client_id,
            "code": code,
            "grant_type": "authorization_code",
             # NO redirect_uri here either
        }
        token_headers = {
            "User-Agent": login_headers["User-Agent"], 
            # Content-Type needed for form data
            "Content-Type": "application/x-www-form-urlencoded", 
        }

        print(f"Exchanging code for token via {token_url}...")
        # Send payload as data (form-encoded), not json
        r_token = self.s.post(token_url, data=token_payload, headers=token_headers)

        if r_token.status_code != 200:
            raise ConnectionError(f"Token exchange failed ({r_token.status_code}): {r_token.text}")
        
        print("Token exchange successful.")

        # convert to JSON
        r_data = r_token.json()

        # save all tokens with access_token expiry date
        self.access_token = r_data.get('access_token')
        self.refresh_token = r_data.get('refresh_token')
        expires_in = r_data.get('expires_in')
        
        if not self.access_token or not self.refresh_token or expires_in is None:
            raise BeatsourceError(f"Token response missing required fields: {r_data}")

        self.expires = datetime.now() + timedelta(seconds=expires_in)

        return r_data
        # --- End New Auth Flow ---

    def refresh(self):
        print("Attempting token refresh...")
        token_url = f"{self.API_URL}auth/o/token/"
        refresh_payload = {
            'client_id': self.client_id,
            'refresh_token': self.refresh_token,
            'grant_type': 'refresh_token',
        }
        # Use a basic user-agent, similar to auth flow
        refresh_headers = {
             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
             "Content-Type": "application/x-www-form-urlencoded",
        }
        
        r = self.s.post(token_url, data=refresh_payload, headers=refresh_headers)

        if r.status_code != 200:
            print(f"Token refresh failed ({r.status_code}): {r.text}")
            # Return the error details for the caller to handle
            try:
                return r.json() 
            except json.JSONDecodeError:
                return {"error": "refresh_failed", "detail": r.text}

        r_data = r.json()
        # Update tokens and expiry
        self.access_token = r_data.get('access_token')
        # Sometimes the refresh token itself is refreshed
        self.refresh_token = r_data.get('refresh_token', self.refresh_token) 
        expires_in = r_data.get('expires_in')

        if not self.access_token or expires_in is None:
            print("Token refresh response missing required fields.")
            return {"error": "invalid_refresh_response", "detail": r_data}

        self.expires = datetime.now() + timedelta(seconds=expires_in)
        print("Token refresh successful.")
        # Return None to indicate success, matching previous potential return value
        return None 

    def set_session(self, session: dict):
        self.access_token = session.get('access_token')
        self.refresh_token = session.get('refresh_token')
        self.expires = session.get('expires')

    def get_session(self):
        return {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'expires': self.expires
        }

    def _get(self, endpoint: str, params: dict = None):
        # function for API requests
        if not params:
            params = {}

        r = self.s.get(f'{self.API_URL}{endpoint}', params=params, headers=self.headers(use_access_token=True))

        # access_token expired
        if r.status_code == 401:
            raise ValueError(r.text)

        # check if territory is not allowed
        if r.status_code == 403:
            detail = r.json().get("detail", "")
            if isinstance(detail, str) and "Territory" in detail:
                raise BeatsourceError("region locked")

        if r.status_code not in {200, 201, 202}:
            raise ConnectionError(f"HTTP {r.status_code}: {r.text}")

        return r.json()

    def get_account(self):
        return self._get('auth/o/introspect')

    def get_track(self, track_id: str):
        return self._get(f'catalog/tracks/{track_id}')

    def get_release(self, release_id: str):
        return self._get(f'catalog/releases/{release_id}')

    def get_release_tracks(self, release_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/releases/{release_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_playlist(self, playlist_id: str):
        return self._get(f'catalog/playlists/{playlist_id}')

    def get_playlist_tracks(self, playlist_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/playlists/{playlist_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_chart(self, chart_id: str):
        return self._get(f'catalog/charts/{chart_id}')

    def get_chart_tracks(self, chart_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/charts/{chart_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_artist(self, artist_id: str):
        return self._get(f'catalog/artists/{artist_id}')

    def get_artist_tracks(self, artist_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/artists/{artist_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_label(self, label_id: str):
        return self._get(f'catalog/labels/{label_id}')

    def get_label_releases(self, label_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/labels/{label_id}/releases', params={
            'page': page,
            'per_page': per_page
        })

    def get_label_tracks(self, label_id: str, page: int = 1, per_page: int = 100):
        return self._get(f'catalog/labels/{label_id}/tracks', params={
            'page': page,
            'per_page': per_page
        })

    def get_search(self, query: str, search_type: str = 'tracks', per_page: int = 50):
        return self._get('catalog/search', params={'q': query, 'type': search_type, 'per_page': per_page})

    def get_track_stream(self, track_id: str):
        # get the 128k stream (.m3u8) for a given track id from needledrop.beatport.com
        return self._get(f'catalog/tracks/{track_id}/stream')

    def get_track_download(self, track_id: str, quality: str):
        # get the 256k stream (.mp4) for a given track id
        return self._get(f'catalog/tracks/{track_id}/download', params={'quality': quality})
