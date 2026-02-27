import logging
import re

from datetime import datetime

from utils.models import *
from utils.models import AlbumInfo
from .beatsource_api import BeatsourceApi, BeatsourceError

module_information = ModuleInformation(
    service_name="Beatsource",
    module_supported_modes=ModuleModes.download | ModuleModes.covers,
    login_behaviour=ManualEnum.manual,
    session_settings={"username": "", "password": ""},
    session_storage_variables=["access_token", "refresh_token", "expires"],
    netlocation_constant="beatsource",
    url_decoding=ManualEnum.manual,
    test_url="https://www.beatsource.com/track/sweet-caroline/11575544"
)


class ModuleInterface:
    # noinspection PyTypeChecker
    def __init__(self, module_controller: ModuleController):
        self.exception = module_controller.module_error
        self.disable_subscription_check = module_controller.orpheus_options.disable_subscription_check
        self.oprinter = module_controller.printer_controller
        self.print = module_controller.printer_controller.oprint
        self.module_controller = module_controller
        self.cover_size = module_controller.orpheus_options.default_cover_options.resolution

        # MINIMUM-MEDIUM = 128kbit/s AAC, HIGH = 256kbit/s AAC, LOSSLESS-HIFI = FLAC 44.1/16
        self.quality_parse = {
            QualityEnum.MINIMUM: "medium",
            QualityEnum.LOW: "medium",
            QualityEnum.MEDIUM: "medium",
            QualityEnum.HIGH: "medium",
            QualityEnum.LOSSLESS: "medium",
            QualityEnum.HIFI: "medium",
            QualityEnum.ATMOS: "medium"
        }

        self.session = BeatsourceApi()
        session = {
            "access_token": module_controller.temporary_settings_controller.read("access_token"),
            "refresh_token": module_controller.temporary_settings_controller.read("refresh_token"),
            "expires": module_controller.temporary_settings_controller.read("expires")
        }

        self.session.set_session(session)

        if session["refresh_token"] is None:
            # old beatsource version with cookies and no refresh token, trigger login manually
            session = self.login(module_controller.module_settings["username"],
                                 module_controller.module_settings["password"])

        if session["refresh_token"] is not None and datetime.now() > session["expires"]:
            # access token expired, get new refresh token
            self.refresh_login()

        try:
            self.valid_account()
        except Exception as e:
            # Subscription check failed (expired account, no Link, etc.).
            # Clear stored session and re-login with credentials - user may have new account.
            err_msg = str(e).lower()
            if "subscription" in err_msg or "link" in err_msg:
                self.module_controller.temporary_settings_controller.set("access_token", None)
                self.module_controller.temporary_settings_controller.set("refresh_token", None)
                self.module_controller.temporary_settings_controller.set("expires", None)
                self.login(self.module_controller.module_settings["username"],
                          self.module_controller.module_settings["password"])
            else:
                raise

    def _save_session(self) -> dict:
        # save the new access_token, refresh_token and expires in the temporary settings
        self.module_controller.temporary_settings_controller.set("access_token", self.session.access_token)
        self.module_controller.temporary_settings_controller.set("refresh_token", self.session.refresh_token)
        self.module_controller.temporary_settings_controller.set("expires", self.session.expires)

        return {
            "access_token": self.session.access_token,
            "refresh_token": self.session.refresh_token,
            "expires": self.session.expires
        }

    def refresh_login(self):
        logging.debug(f"Beatsource: access_token expired, getting a new one")

        # get a new access_token and refresh_token from the API
        refresh_data = self.session.refresh()
        if refresh_data:
            # Refresh failed (invalid_grant, expired, revoked, account changed, etc.).
            # Clear stored session and re-login with credentials from settings.
            # Handles: expired subscriptions, new accounts, password changes.
            self.module_controller.temporary_settings_controller.set("access_token", None)
            self.module_controller.temporary_settings_controller.set("refresh_token", None)
            self.module_controller.temporary_settings_controller.set("expires", None)
            self.login(self.module_controller.module_settings["username"],
                       self.module_controller.module_settings["password"])
            return

        self._save_session()
            
    def login(self, email: str, password: str):
        logging.debug(f"Beatsource: no session found, login")
        
        # Check if credentials are provided
        if not email or not password:
            raise self.exception(
                "Beatsource credentials are missing in settings.json. "
                "Please fill in: username, password. "
                "Use the OrpheusDL GUI Settings tab (Beatsource) or edit config/settings.json directly."
            )
        
        login_data = self.session.auth(email, password)

        if login_data.get("error_description") is not None:
            error_desc = login_data.get("error_description")
            # Check for blank field errors and provide a better message
            if isinstance(error_desc, dict):
                if "username" in error_desc and "password" in error_desc:
                    if any("blank" in str(msg).lower() for msg in error_desc.get("username", [])) and \
                       any("blank" in str(msg).lower() for msg in error_desc.get("password", [])):
                        raise self.exception(
                            "Beatsource credentials are missing in settings.json. "
                            "Please fill in: username, password. "
                            "Use the OrpheusDL GUI Settings tab (Beatsource) or edit config/settings.json directly."
                        )
            raise self.exception(error_desc)

        self.valid_account()

        return self._save_session()

    def valid_account(self):
        if not self.disable_subscription_check:
            # get the subscription from the API and check if it's at least a "Link" subscription
            account_data = self.session.get_account()
            logging.debug(f"Beatsource: Account data: {account_data}")
            if not account_data.get("subscription"):
                raise self.exception("Beatsource: Account does not have an active 'Link' subscription")

            # Essentials = "bp_basic", Professional = "bp_link_pro" or "bsrc_link_pro_plus"
            sub = account_data.get("subscription", "").lower()
            if sub == "bp_link_pro" or "pro" in sub:
                # Pro subscription, set the quality to high and lossless
                self.print("Beatsource: Professional subscription detected, allowing high and lossless quality")
                self.quality_parse[QualityEnum.HIGH] = "high"
                self.quality_parse[QualityEnum.HIFI] = "lossless"
                self.quality_parse[QualityEnum.LOSSLESS] = "lossless"

    @staticmethod
    def custom_url_parse(link: str):
        # Regex updated to capture the final numeric ID in the path
        match = re.search(r"https?://(?:www\.)?beatsource\.com/(?:[a-z]{2}/)?(?P<type>track|release|artist|playlist|playlists|chart|label).*/(?P<id>\d+)[^/]*?(?:$|\?)", link)

        if not match:
            # Handle cases where the regex doesn't match (e.g., invalid URL format)
            logging.error(f"Beatsource: Could not parse URL type/ID from: {link}")
            raise ValueError(f"Could not parse Beatsource URL: {link}")

        # Map captured type to DownloadTypeEnum
        captured_type = match.group("type")
        media_types = {
            "track": DownloadTypeEnum.track,
            "release": DownloadTypeEnum.album,
            "artist": DownloadTypeEnum.artist,
            "playlist": DownloadTypeEnum.playlist,
            "playlists": DownloadTypeEnum.playlist,
            "chart": DownloadTypeEnum.playlist,
            "label": DownloadTypeEnum.label
        }

        media_type_enum = media_types.get(captured_type)
        if not media_type_enum:
             logging.error(f"Beatsource: Unknown media type '{captured_type}' parsed from URL: {link}")
             raise ValueError(f"Unknown Beatsource media type in URL: {link}")
             # return None

        media_id = match.group("id")

        return MediaIdentification(
            media_type=media_type_enum,
            media_id=media_id
        )

    @staticmethod
    def _generate_artwork_url(cover_url: str, size: int, max_size: int = 1400):
        # if more than max_size are requested, cap the size at max_size
        if size > max_size:
            size = max_size

        # check if it"s a dynamic_uri, if not make it one
        res_pattern = re.compile(r"\d{3,4}x\d{3,4}")
        match = re.search(res_pattern, cover_url)
        if match:
            # replace the hardcoded resolution with dynamic one
            cover_url = re.sub(res_pattern, "{w}x{h}", cover_url)

        # replace the dynamic_uri h and w parameter with the wanted size
        return cover_url.format(w=size, h=size)

    def search(self, query_type: DownloadTypeEnum, query: str, track_info: TrackInfo = None, limit: int = 20):
        # Map DownloadTypeEnum to API search type string
        api_search_type_map = {
            DownloadTypeEnum.track: "tracks",
            DownloadTypeEnum.album: "releases",
            DownloadTypeEnum.playlist: "charts",
            DownloadTypeEnum.artist: "artists",
            DownloadTypeEnum.label: "labels"
        }
        api_search_type = api_search_type_map.get(query_type)
        if not api_search_type:
            raise self.exception(f"Query type '{query_type.name}' is not supported for Beatsource search!")

        # Call API with the correct type and limit
        results = self.session.get_search(query, search_type=api_search_type, per_page=limit)
        logging.debug(f"Beatsource API Search Response (type={api_search_type}): {results}")

        # Use the API type string as the key to get results
        # (This replaces the old name_parse dictionary)
        search_key = api_search_type

        items = []
        if search_key:
            # Ensure we iterate over an empty list if the key is missing in the results
            for i in results.get(search_key, []):
                additional = []
                duration = None
                item_extra_kwargs = {"data": {i.get("id"): i}}
                
                # Extract cover image URL (use smaller size for search thumbnails)
                # For tracks: use release.image (album cover), not image (which is waveform)
                # For other types: use image directly
                if query_type is DownloadTypeEnum.track:
                    release_data = i.get('release') or {}
                    image_data = release_data.get('image') or {}
                else:
                    image_data = i.get('image') or {}
                image_uri = image_data.get('uri') or image_data.get('dynamic_uri') if isinstance(image_data, dict) else None
                image_url = self._generate_artwork_url(image_uri, 56) if image_uri else None
                
                # Fallback to Beatsource default artist/label cover if no image available
                if not image_url and query_type in (DownloadTypeEnum.artist, DownloadTypeEnum.label):
                    image_url = "https://www.beatsource.com/static/images/Cover_Artist.jpg"
                
                # Extract preview/sample URL
                preview_url = i.get('sample_url') or i.get('preview_url') or i.get('sample', {}).get('url')
                
                if query_type is DownloadTypeEnum.playlist:
                    artists = [i.get("person").get("owner_name") if i.get("person") else "Beatsource"]
                    year = i.get("change_date")[:4] if i.get("change_date") else None
                    if i.get("track_count") is not None:
                        tc = i.get('track_count')
                        additional.append(f"1 track" if tc == 1 else f"{tc} tracks")
                elif query_type is DownloadTypeEnum.track:
                    artists = [a.get("name") for a in i.get("artists")]
                    year = i.get("publish_date")[:4] if i.get("publish_date") else None

                    duration = i.get("length_ms") // 1000
                    if i.get("bpm"):
                        additional.append(f"{i.get('bpm')} BPM")
                    # Store track slug for URL build (Beatsource URLs are /track/slug/id)
                    if i.get("slug"):
                        item_extra_kwargs["track_slug"] = i.get("slug")
                elif query_type is DownloadTypeEnum.album:
                    artists = [j.get("name") for j in i.get("artists")]
                    year = i.get("publish_date")[:4] if i.get("publish_date") else None
                    if i.get("track_count") is not None:
                        tc = i.get('track_count'); additional.append(f"1 track" if tc == 1 else f"{tc} tracks")
                elif query_type is DownloadTypeEnum.artist:
                    artists = None
                    year = None
                    # Store artist slug for proper URL generation (Beatsource requires slug in URL)
                    if i.get("slug"):
                        item_extra_kwargs["artist_slug"] = i.get("slug")
                    elif i.get("name"):
                        item_extra_kwargs["artist_slug"] = i.get("name").lower().replace(" ", "-")
                elif query_type is DownloadTypeEnum.label:
                    # Skip only when API explicitly reports 0 releases (empty label); if count missing, still show
                    rc = i.get("releases_count") or i.get("release_count")
                    if rc is not None and rc == 0:
                        continue
                    artists = [i.get("name")] if i.get("name") else None
                    date_val = i.get("founded") or i.get("created_at") or i.get("founded_date")
                    year = date_val[:4] if (date_val and isinstance(date_val, str) and len(date_val) >= 4) else (str(getattr(date_val, 'year', '')) if date_val and hasattr(date_val, 'year') else None)
                    if i.get("slug"):
                        item_extra_kwargs["label_slug"] = i.get("slug")
                    elif i.get("name"):
                        item_extra_kwargs["label_slug"] = i.get("name").lower().replace(" ", "-")
                    if rc is not None:
                        additional.append(f"1 release" if rc == 1 else f"{rc} releases")
                else:
                    raise self.exception(f"Query type '{query_type.name}' is not supported!")

                name = i.get("name")
                name += f" ({i.get('mix_name')})" if i.get("mix_name") else ""

                additional.append(f"Exclusive") if i.get("exclusive") is True else None

                if query_type is DownloadTypeEnum.playlist and (i.get("track_count") is None or i.get("track_count") == 0):
                    continue

                item = SearchResult(
                    name=name,
                    artists=artists,
                    year=year,
                    duration=duration,
                    result_id=i.get("id"),
                    additional=additional if additional != [] else None,
                    image_url=image_url,
                    preview_url=preview_url,
                    extra_kwargs=item_extra_kwargs
                )

                items.append(item)

        return items

    def get_playlist_info(self, playlist_id: str, **kwargs) -> PlaylistInfo:
        playlist_data = None
        playlist_tracks_data = None
        is_chart_endpoint = False # Keep track of which endpoint succeeded

        # --- Attempt to fetch as a standard playlist first ---
        try:
            logging.debug(f"Beatsource: Fetching playlist info as playlist ID: {playlist_id}")
            playlist_data = self.session.get_playlist(playlist_id)
            playlist_tracks_data = self.session.get_playlist_tracks(playlist_id)
        except ConnectionError as e:
            # If it's a 404, try the chart endpoint
            if "404" in str(e) or "Not found" in str(e):
                logging.debug(f"Beatsource: Fetching as playlist failed (404). Trying chart endpoint for ID: {playlist_id}")
                is_chart_endpoint = True
                try:
                    logging.debug(f"Beatsource: Re-fetching playlist info as chart ID: {playlist_id}")
                    playlist_data = self.session.get_chart(playlist_id)
                    playlist_tracks_data = self.session.get_chart_tracks(playlist_id)
                except ConnectionError as e2:
                    # If the second attempt also fails, raise the second error
                    raise self.exception(f"Failed to get playlist info for {playlist_id} (tried both playlist and chart endpoints): {e2}")
                except Exception as e_retry:
                    # Catch other errors on retry
                    raise self.exception(f"Unexpected error fetching playlist info for {playlist_id} as chart: {e_retry}")
            else:
                # If it's not a 404 error, re-raise the original error
                raise self.exception(f"Failed to get playlist info for {playlist_id} (playlist endpoint): {e}")
        except Exception as e_initial:
             # Catch other errors on initial playlist attempt
             raise self.exception(f"Unexpected error fetching playlist info for {playlist_id} (playlist endpoint): {e_initial}")

        # --- Check if data was successfully fetched --- 
        if playlist_data is None or playlist_tracks_data is None:
            raise self.exception(f"Could not retrieve playlist data for {playlist_id} after attempts.")

        # --- Processing logic (adapts based on which endpoint succeeded) --- 
        cache = {"data": {}}
        if is_chart_endpoint:
            # Chart endpoint response structure might be different (results directly contain tracks)
            playlist_tracks = playlist_tracks_data.get("results", [])
        else:
            # Playlist endpoint response structure (tracks are nested under "track")
            playlist_tracks = [t.get("track") for t in playlist_tracks_data.get("results", []) if t.get("track")]

        total_tracks = playlist_tracks_data.get("count")
        # Ensure total_tracks is an integer before calculating pages
        if not isinstance(total_tracks, int) or total_tracks <= 0:
             logging.warning(f"Beatsource: Invalid or missing 'count' in playlist tracks response for {playlist_id}. Assuming only first page.")
             total_tracks = len(playlist_tracks) # Use the count from the first page as fallback
        
        # Fetch remaining pages if necessary
        if total_tracks > len(playlist_tracks):
             num_fetched = len(playlist_tracks)
             per_page = 100 # Assuming the API uses 100 per page
             for page in range(2, (total_tracks - 1) // per_page + 2):
                 print(f"Fetching {num_fetched}/{total_tracks}") 
                 try:
                     if is_chart_endpoint:
                         paged_tracks_data = self.session.get_chart_tracks(playlist_id, page=page)
                         new_tracks = paged_tracks_data.get("results", [])
                     else:
                         paged_tracks_data = self.session.get_playlist_tracks(playlist_id, page=page)
                         new_tracks = [t.get("track") for t in paged_tracks_data.get("results", []) if t.get("track")]
                     
                     if not new_tracks:
                          logging.warning(f"Beatsource: No more tracks found on page {page} for playlist {playlist_id}. Expected {total_tracks} total.")
                          break
                     playlist_tracks.extend(new_tracks)
                     num_fetched += len(new_tracks)
                     if num_fetched >= total_tracks:
                          break
                 except ConnectionError as e:
                      logging.error(f"Beatsource: Failed to fetch page {page} for playlist {playlist_id} (endpoint: {'chart' if is_chart_endpoint else 'playlist'}): {e}")
                      break
                 except Exception as e_page:
                      logging.error(f"Beatsource: Unexpected error fetching page {page} for playlist {playlist_id}: {e_page}")
                      break

        # Re-populate cache with all fetched tracks and add numbering
        cache = {"data": {}}
        actual_total_tracks = len(playlist_tracks)
        for i, track in enumerate(playlist_tracks):
            if track and track.get("id"): # Ensure track and track ID are valid
                 track["track_number"] = i + 1
                 track["total_tracks"] = actual_total_tracks
                 cache["data"][track.get("id")] = track
            else:
                 logging.warning(f"Beatsource: Skipping invalid track data at index {i} in playlist {playlist_id}")

        # Process playlist metadata (adapts based on endpoint)
        if is_chart_endpoint:
            creator = playlist_data.get("person", {}).get("owner_name") or "Beatsource"
            release_year = playlist_data.get("change_date")[:4] if playlist_data.get("change_date") else None
            cover_url_raw = playlist_data.get("image", {}).get("dynamic_uri")
        else:
            creator = "User" # Default for standard playlists
            release_year = playlist_data.get("updated_date")[:4] if playlist_data.get("updated_date") else None
            # Handle potentially missing cover art for standard playlists
            cover_url_raw = None
            release_images = playlist_data.get("release_images")
            if release_images and isinstance(release_images, list) and len(release_images) > 0:
                 img = release_images[0]
                 if isinstance(img, dict) and "dynamic_uri" in img:
                     cover_url_raw = img.get("dynamic_uri")
                 elif isinstance(img, str):
                     cover_url_raw = img
        
        generated_cover_url = None
        if cover_url_raw:
             try:
                 generated_cover_url = self._generate_artwork_url(cover_url_raw, self.cover_size)
             except Exception as e_cover:
                 logging.error(f"Beatsource: Failed to generate artwork URL for playlist {playlist_id} from '{cover_url_raw}': {e_cover}")
        else:
             logging.warning(f"Beatsource: No cover image found for playlist {playlist_id}")

        # Filter out None/invalid tracks before calculating duration or getting IDs
        valid_tracks = [t for t in playlist_tracks if t and t.get("id") and t.get("length_ms") is not None]

        return PlaylistInfo(
            name=playlist_data.get("name"),
            creator=creator,
            release_year=release_year,
            duration=sum([(t.get("length_ms") or 0) // 1000 for t in valid_tracks]),
            tracks=[t.get("id") for t in valid_tracks],
            cover_url=generated_cover_url,
            track_extra_kwargs=cache
        )

    def get_artist_info(self, artist_id: str, get_credited_albums: bool) -> ArtistInfo:
        artist_data = self.session.get_artist(artist_id)
        artist_tracks_data = self.session.get_artist_tracks(artist_id)

        # now fetch all the found total_items
        artist_tracks = artist_tracks_data.get("results")
        total_tracks = artist_tracks_data.get("count")
        for page in range(2, total_tracks // 100 + 2):
            artist_tracks += self.session.get_artist_tracks(artist_id, page=page).get("results")

        return ArtistInfo(
            name=artist_data.get("name"),
            tracks=[t.get("id") for t in artist_tracks],
            track_extra_kwargs={"data": {t.get("id"): t for t in artist_tracks}},
        )

    def get_label_info(self, label_id: str, get_credited_albums: bool = True, **kwargs) -> ArtistInfo:
        """Return label metadata, releases (as albums), and tracks as ArtistInfo for consistent download flow."""
        label_data = self.session.get_label(label_id)
        label_name = label_data.get("name") or "Unknown Label"

        label_tracks = []
        try:
            tracks_data = self.session.get_label_tracks(label_id)
            label_tracks = list(tracks_data.get("results") or [])
            total_tracks = tracks_data.get("count") or len(label_tracks)
            for page in range(2, total_tracks // 100 + 2):
                label_tracks += self.session.get_label_tracks(label_id, page=page, per_page=100).get("results") or []
        except Exception:
            pass

        releases_list = []
        try:
            releases_data = self.session.get_label_releases(label_id)
            releases_list = list(releases_data.get("results") or [])
            total_releases = releases_data.get("count") or len(releases_list)
            for page in range(2, total_releases // 100 + 2):
                releases_list += self.session.get_label_releases(label_id, page=page, per_page=100).get("results") or []
        except Exception:
            pass

        release_ids = [str(r.get("id")) for r in releases_list if r.get("id") is not None]
        track_ids = [t.get("id") for t in label_tracks if t.get("id") is not None]
        album_data = {str(r.get("id")): r for r in releases_list if r.get("id") is not None}
        track_data = {t.get("id"): t for t in label_tracks if t.get("id") is not None}

        return ArtistInfo(
            name=label_name,
            artist_id=label_id,
            albums=release_ids,
            album_extra_kwargs={"data": album_data},
            tracks=track_ids,
            track_extra_kwargs={"data": track_data},
        )

    def get_album_info(self, album_id: str, data=None, is_chart: bool = False) -> AlbumInfo | None:
        # check if album is already in album cache, add it
        if data is None:
            data = {}

        try:
            album_data = data.get(album_id) if album_id in data else self.session.get_release(album_id)
        except BeatsourceError as e:
            self.print(f"Beatsource: Album {album_id} is {str(e)}")
            return

        tracks_data = self.session.get_release_tracks(album_id)

        # now fetch all the found total_items
        tracks = tracks_data.get("results")
        total_tracks = tracks_data.get("count")
        for page in range(2, total_tracks // 100 + 2):
            print(f"Fetching {len(tracks)}/{total_tracks}", end="\r")
            tracks += self.session.get_release_tracks(album_id, page=page).get("results")

        cache = {"data": {album_id: album_data}}
        for i, track in enumerate(tracks):
            # add the track numbers
            track["number"] = i + 1
            # add the modified track to the track_extra_kwargs
            cache["data"][track.get("id")] = track

        album_artists = album_data.get("artists") or album_data.get("remixers") or album_data.get("remixer")
        # If no album artists found, try to get it from the first track
        if not album_artists and tracks:
            first_track = tracks[0]
            album_artists = first_track.get("artists") or first_track.get("bsrc_remixer")

        return AlbumInfo(
            name=album_data.get("name"),
            release_year=album_data.get("publish_date")[:4] if album_data.get("publish_date") else None,
            # sum up all the individual track lengths
            duration=sum([t.get("length_ms") // 1000 for t in tracks]),
            upc=album_data.get("upc"),
            cover_url=self._generate_artwork_url(album_data.get("image").get("dynamic_uri"), self.cover_size),
            artist=(album_artists or [{}])[0].get("name"),
            artist_id=(album_artists or [{}])[0].get("id"),
            tracks=[t.get("id") for t in tracks],
            track_extra_kwargs=cache,
        )

    def get_track_info(self, track_id: str, quality_tier: QualityEnum, codec_options: CodecOptions, slug: str = None,
                       data=None, is_chart: bool = False) -> TrackInfo:
        if data is None:
            data = {}

        # Support both str and int keys (artist/playlist track_data often has int ids from API)
        track_data = data.get(track_id) or (data.get(int(track_id)) if isinstance(track_id, str) and track_id.isdigit() else None)
        if track_data is None:
            track_data = self.session.get_track(track_id)

        album_id = track_data.get("release").get("id")
        album_data = {}
        error = None

        try:
            album_data = data[album_id] if album_id in data else self.session.get_release(album_id)
        except ConnectionError as e:
            # check if the album is region locked
            if "Territory Restricted." in str(e):
                error = f"Album {album_id} is region locked"

        track_name = track_data.get("name")
        track_name += f" ({track_data.get('mix_name')})" if track_data.get("mix_name") else ""

        release_year = track_data.get("publish_date")[:4] if track_data.get("publish_date") else None
        genres = [track_data.get("genre").get("name")]
        # check if a second genre exists
        genres += [track_data.get("sub_genre").get("name")] if track_data.get("sub_genre") else []

        extra_tags = {}
        if track_data.get("bpm"):
            extra_tags["BPM"] = str(track_data.get("bpm"))
        if track_data.get("key"):
            extra_tags["Key"] = track_data.get("key").get("name")
        if track_data.get("catalog_number"):
            extra_tags["Catalog number"] = track_data.get("catalog_number")

        track_artists = track_data.get("artists") or track_data.get("remixers") or track_data.get("remixer") or track_data.get("bsrc_remixer")
        # Fallback to using track artists for album artist if album artist is missing
        album_artists = album_data.get("artists") or album_data.get("remixers") or album_data.get("remixer") or track_artists

        tags = Tags(
            album_artist=(album_artists or [{}])[0].get("name"),
            track_number=track_data.get("number"),
            total_tracks=album_data.get("track_count"),
            upc=album_data.get("upc"),
            isrc=track_data.get("isrc"),
            genres=genres,
            release_date=track_data.get("publish_date"),
            copyright=f"© {release_year} {track_data.get('release').get('label').get('name')}",
            label=track_data.get("release").get("label").get("name"),
            extra_tags=extra_tags
        )

        if not track_data["is_available_for_streaming"]:
            error = f"Track '{track_data.get('name')}' is not streamable!"
        elif track_data.get("preorder"):
            error = f"Track '{track_data.get('name')}' is not yet released!"

        # Determine codec, bitrate, bit_depth directly from quality_tier argument
        if quality_tier in [QualityEnum.LOSSLESS, QualityEnum.HIFI]:
            codec = CodecEnum.FLAC
            bitrate = 1411 # Approx FLAC bitrate
            bit_depth = 16
        else:
            codec = CodecEnum.AAC
            # Determine AAC bitrate (e.g., 256 for HIGH, 128 otherwise based on original quality_parse logic)
            if quality_tier == QualityEnum.HIGH and self.quality_parse[QualityEnum.HIGH] == "high": # Check if HIGH is enabled
                 bitrate = 256
            else: # MEDIUM, LOW, MINIMUM or HIGH not enabled
                 bitrate = 128
            bit_depth = None # Not applicable for lossy AAC in this context
        
        length_ms = track_data.get("length_ms")

        # Extract preview/sample URL (same as search; enables album track list preview in GUI)
        preview_url = track_data.get('sample_url') or track_data.get('preview_url') or (track_data.get('sample') or {}).get('url')

        track_info = TrackInfo(
            name=track_name,
            album=album_data.get("name"),
            album_id=album_data.get("id"),
            artists=[a.get("name") for a in (track_artists or [])],
            artist_id=(track_artists or [{}])[0].get("id"),
            id=str(track_id),
            release_year=release_year,
            duration=length_ms // 1000 if length_ms else None,
            bitrate=bitrate, # Use determined bitrate
            bit_depth=bit_depth, # Use determined bit_depth
            sample_rate=44.1,
            cover_url=self._generate_artwork_url(
                track_data.get("release").get("image").get("dynamic_uri"), self.cover_size),
            tags=tags,
            codec=codec, # Use determined codec
            download_extra_kwargs={"track_id": track_id, "quality_tier": quality_tier},
            error=error,
            preview_url=preview_url
        )

        return track_info

    def get_track_cover(self, track_id: str, cover_options: CoverOptions, data=None) -> CoverInfo:
        if data is None:
            data = {}

        track_data = data.get(track_id) or (data.get(int(track_id)) if isinstance(track_id, str) and track_id.isdigit() else None)
        if track_data is None:
            track_data = self.session.get_track(track_id)
        cover_url = track_data.get("release").get("image").get("dynamic_uri")

        return CoverInfo(
            url=self._generate_artwork_url(cover_url, cover_options.resolution),
            file_type=ImageFileTypeEnum.jpg)

    def get_track_download(self, track_id: str, quality_tier: QualityEnum) -> TrackDownloadInfo:
        # Determine requested quality based on the quality_tier argument passed by Orpheus
        # Use the parsed quality string (high, medium, lossless) which handles subscription checks
        request_quality = self.quality_parse[quality_tier] 
            
        stream_data = self.session.get_track_download(track_id, request_quality)

        if not stream_data.get("location"):
            raise self.exception("Could not get stream, exiting")

        return TrackDownloadInfo(
            download_type=DownloadEnum.URL,
            file_url=stream_data.get("location")
        )
